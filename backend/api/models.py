"""模型管理：列出、切換、下載 GGUF。

llama-server 一個行程只服務一顆模型，「切換」＝砍掉現有行程、換 gguf 重開。
行程可能是使用者自己開的（開發時跑 ps1），不一定是這裡生的子行程，
所以要砍的對象用 port 去找，不能只記自己的 Popen。

下載與啟動都在背景執行緒進行，端點立刻回覆；前端輪詢 GET /models 看進度。
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import actions, config, db
from ..core import llm

log = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])

# 可下載的型錄。官方 Qwen 未提供 GGUF，用 unsloth 的量化版（同 README 第 5 節）。
# 型錄外的模型走 /models/download-url，貼 Hugging Face 的 .gguf 連結自行下載。
# mmproj = 視覺投影檔：Qwen3.5 全系列都是多模態，llama-server 掛上它才能吃圖片，
# 匯入履歷時會附頁面截圖給模型看排版。
CATALOG = [
    {"name": "Qwen3.5-35B-A3B-Q4_K_M", "size_gb": 20.0,
     "note": "最強選項（MoE，啟用 3B），需 64GB RAM 或大顯存",
     "url": "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/Qwen3.5-35B-A3B-Q4_K_M.gguf",
     "mmproj": "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf"},
    {"name": "Qwen3.5-27B-Q4_K_M", "size_gb": 16.5,
     "note": "更強的判讀，需 24GB 級顯卡；8GB 顯卡會極慢",
     "url": "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-Q4_K_M.gguf",
     "mmproj": "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/mmproj-F16.gguf"},
    {"name": "Qwen3.5-9B-Q4_K_M", "size_gb": 5.3, "note": "預設，判讀最準（約需 7.4 GB VRAM）",
     "url": "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf",
     "mmproj": "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf"},
    {"name": "Qwen3.5-4B-Q4_K_M", "size_gb": 2.6, "note": "較省資源，複雜表格易誤判",
     "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
     "mmproj": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/mmproj-F16.gguf"},
    {"name": "Qwen3.5-2B-Q4_K_M", "size_gb": 1.5, "note": "無獨顯、純 CPU 的退路",
     "url": "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf",
     "mmproj": "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/mmproj-F16.gguf"},
]


def _mmproj_path(name: str) -> Path:
    return config.MODELS_DIR / f"{name}.mmproj.gguf"

READY_TIMEOUT = 300    # 9B 冷啟動要載 5 GB 進 VRAM，給足時間
DOWNLOAD_TIMEOUT = (15, 60)

_lock = threading.Lock()
_starting: str | None = None            # 正在啟動的模型名，None = 沒有
_downloads: dict[str, dict] = {}        # name -> {"pct": int, "error": str|None}


class SelectIn(BaseModel):
    name: str


@router.get("")
def list_models() -> dict:
    local = {p.stem: p for p in sorted(config.MODELS_DIR.glob("*.gguf"))
             if not p.name.endswith(".mmproj.gguf")}   # 視覺投影檔不是模型，不列
    rows = []
    for entry in CATALOG:
        rows.append(_row(entry["name"], entry["size_gb"], entry["note"],
                         downloaded=entry["name"] in local, downloadable=True,
                         has_mmproj="mmproj" in entry))
        local.pop(entry["name"], None)
    for name, path in local.items():   # 使用者自己放進來的檔案也要列
        rows.append(_row(name, round(path.stat().st_size / 1024 ** 3, 1), "",
                         downloaded=True, downloadable=False))
    for name in _downloads:            # 自訂網址下載中（或失敗）的也要列
        if not any(r["name"] == name for r in rows):
            rows.append(_row(name, 0, "自訂下載", downloaded=False, downloadable=False))
    return {"active": config.LLM_MODEL,
            "running": llm.available(config.LLM_HOST),
            "starting": _starting,
            "vision": llm.supports_vision(config.LLM_HOST),
            "models": rows}


def _row(name: str, size_gb: float, note: str, downloaded: bool, downloadable: bool,
         has_mmproj: bool = False) -> dict:
    dl = _downloads.get(name)
    vision = _mmproj_path(name).exists()
    return {"name": name, "size_gb": size_gb, "note": note,
            "downloaded": downloaded, "downloadable": downloadable,
            "active": name == config.LLM_MODEL,
            "vision": vision,
            # 型錄有視覺檔、主檔已下載但視覺檔還沒抓 → 前端顯示「補視覺檔」
            "vision_downloadable": has_mmproj and downloaded and not vision,
            "downloading": bool(dl and dl["error"] is None),
            "progress": dl["pct"] if dl else 0,
            "error": (dl["error"] if dl else None) or ""}


@router.post("/select")
def select_model(body: SelectIn) -> dict:
    global _starting
    gguf = config.MODELS_DIR / f"{body.name}.gguf"
    if not gguf.exists():
        raise HTTPException(404, "這顆模型還沒下載")
    if not config.LLAMA_SERVER.exists():
        raise HTTPException(500, f"找不到 {config.LLAMA_SERVER}，請先取得 llama.cpp（見 README 第 5 節）")
    with _lock:
        if _starting:
            raise HTTPException(409, f"「{_starting}」正在啟動中，請稍候")
        # 同一顆模型也可能需要重啟：剛補下載視覺檔時，跑著的引擎還沒掛上它
        vision_ok = _mmproj_path(body.name).exists() == llm.supports_vision(config.LLM_HOST)
        if body.name == config.LLM_MODEL and llm.available(config.LLM_HOST) and vision_ok:
            return {"ok": True}
        _starting = body.name
    threading.Thread(target=_switch, args=(body.name, gguf), daemon=True).start()
    return {"ok": True}


def _switch(name: str, gguf) -> None:
    global _starting
    port = urlparse(config.LLM_HOST).port or 8085
    try:
        _kill_port(port)
        args = [str(config.LLAMA_SERVER), "-m", str(gguf), "--port", str(port),
                "--ctx-size", str(config.LLM_CTX_SIZE), "--n-gpu-layers", "999",
                "--jinja", "--temp", "0", "--reasoning", "off"]
        # 視覺投影檔在就掛上，模型才吃得了頁面截圖
        mmproj = _mmproj_path(name)
        if mmproj.exists():
            args += ["--mmproj", str(mmproj)]
        # log 導到獨立檔案：llama-server 的輸出量大且格式不同，混進 app.log 會淹掉一切
        out = (config.LOG_DIR / "llama-server.log").open("w", encoding="utf-8", errors="replace")
        subprocess.Popen(
            args, stdout=out, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)

        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            if llm.available(config.LLM_HOST):
                config.LLM_MODEL = name
                db.put_kv("llm_model", name)   # 後端重啟後記得這個選擇
                actions.record("切換模型「%s」成功", name)
                return
            time.sleep(2)
        actions.problem("切換模型「%s」失敗：等了 %d 秒還沒就緒，詳見 llama-server.log",
                        name, READY_TIMEOUT)
    except Exception as e:
        log.exception("切換模型失敗 %s", name)
        actions.problem("切換模型「%s」失敗：%s", name, e)
    finally:
        _starting = None


def _kill_port(port: int) -> None:
    """結束佔著推論埠的行程（通常是上一顆模型的 llama-server）。"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
         "| Select-Object -ExpandProperty OwningProcess -Unique "
         "| ForEach-Object { Stop-Process -Id $_ -Force }"],
        capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(1)   # 等 port 真正釋放


class UrlIn(BaseModel):
    url: str


@router.post("/download-url")
def download_from_url(body: UrlIn) -> dict:
    """自訂模型：貼 Hugging Face 的 .gguf 連結下載到 models/。

    只做兩件防呆：https、副檔名 .gguf。下載的是資料檔不會被執行，
    這是使用者自己機器上的個人工具，不必更嚴。
    """
    # 檔案頁的 /blob/ 連結幫使用者轉成直接下載的 /resolve/
    url = body.url.strip().replace("/blob/", "/resolve/", 1)
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(422, "只接受 https 網址")
    fname = unquote(Path(parsed.path).name)
    if not fname.lower().endswith(".gguf"):
        raise HTTPException(422, "網址必須指向 .gguf 檔（到 Hugging Face 檔案列表複製下載連結）")

    name = fname[: -len(".gguf")]
    if (config.MODELS_DIR / fname).exists():
        raise HTTPException(409, "已有同名的模型檔")
    with _lock:
        active = _downloads.get(name)
        if active and active["error"] is None:
            raise HTTPException(409, "已經在下載中")
        _downloads[name] = {"pct": 0, "error": None}
    threading.Thread(target=_download, args=({"name": name, "url": url},),
                     daemon=True).start()
    return {"ok": True, "name": name}


@router.post("/download")
def download_model(body: SelectIn) -> dict:
    entry = next((e for e in CATALOG if e["name"] == body.name), None)
    if entry is None:
        raise HTTPException(404, "型錄裡沒有這顆模型")
    have_main = (config.MODELS_DIR / f"{body.name}.gguf").exists()
    need_mmproj = "mmproj" in entry and not _mmproj_path(body.name).exists()
    # 主檔在、視覺檔缺 → 只補視覺檔（早期版本下載的模型沒有 mmproj）
    if have_main and not need_mmproj:
        raise HTTPException(409, "這顆模型已經下載過了")
    with _lock:
        active = _downloads.get(body.name)
        if active and active["error"] is None:
            raise HTTPException(409, "已經在下載中")
        _downloads[body.name] = {"pct": 0, "error": None}
    threading.Thread(target=_download, args=(entry,), daemon=True).start()
    return {"ok": True}


def _download(entry: dict) -> None:
    name = entry["name"]
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        gguf = config.MODELS_DIR / f"{name}.gguf"
        if not gguf.exists():
            _fetch(entry["url"], gguf, name, track=True)
        mm_url = entry.get("mmproj")
        if mm_url and not _mmproj_path(name).exists():
            # 視覺檔比主檔小得多，進度停在 99% 一下就好
            _downloads[name]["pct"] = 99
            _fetch(mm_url, _mmproj_path(name), name, track=False)
        del _downloads[name]
        actions.record("下載模型「%s」成功", name)
    except Exception as e:
        log.exception("下載模型失敗 %s", name)
        _downloads[name]["error"] = str(e)
        actions.problem("下載模型「%s」失敗：%s", name, e)


def _fetch(url: str, dest: Path, name: str, track: bool) -> None:
    """下載到 .part 再改名，中斷不會留下半套檔案。track=True 時回報進度。"""
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with part.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if track and total:
                        _downloads[name]["pct"] = min(99, done * 100 // total)
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise

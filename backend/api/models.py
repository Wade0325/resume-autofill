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
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import actions, config, db
from ..core import llm

log = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])

# 可下載的型錄。官方 Qwen 未提供 GGUF，用 unsloth 的量化版（同 README 第 5 節）。
CATALOG = [
    {"name": "Qwen3.5-9B-Q4_K_M", "size_gb": 5.3, "note": "預設，判讀最準（約需 7.4 GB VRAM）",
     "url": "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"},
    {"name": "Qwen3.5-4B-Q4_K_M", "size_gb": 2.6, "note": "較省資源，複雜表格易誤判",
     "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf"},
    {"name": "Qwen3.5-2B-Q4_K_M", "size_gb": 1.5, "note": "無獨顯、純 CPU 的退路",
     "url": "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf"},
]

READY_TIMEOUT = 300    # 9B 冷啟動要載 5 GB 進 VRAM，給足時間
DOWNLOAD_TIMEOUT = (15, 60)

_lock = threading.Lock()
_starting: str | None = None            # 正在啟動的模型名，None = 沒有
_downloads: dict[str, dict] = {}        # name -> {"pct": int, "error": str|None}


class SelectIn(BaseModel):
    name: str


@router.get("")
def list_models() -> dict:
    local = {p.stem: p for p in sorted(config.MODELS_DIR.glob("*.gguf"))}
    rows = []
    for entry in CATALOG:
        rows.append(_row(entry["name"], entry["size_gb"], entry["note"],
                         downloaded=entry["name"] in local, downloadable=True))
        local.pop(entry["name"], None)
    for name, path in local.items():   # 使用者自己放進來的檔案也要列
        rows.append(_row(name, round(path.stat().st_size / 1024 ** 3, 1), "",
                         downloaded=True, downloadable=False))
    return {"active": config.LLM_MODEL,
            "running": llm.available(config.LLM_HOST),
            "starting": _starting,
            "models": rows}


def _row(name: str, size_gb: float, note: str, downloaded: bool, downloadable: bool) -> dict:
    dl = _downloads.get(name)
    return {"name": name, "size_gb": size_gb, "note": note,
            "downloaded": downloaded, "downloadable": downloadable,
            "active": name == config.LLM_MODEL,
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
        if body.name == config.LLM_MODEL and llm.available(config.LLM_HOST):
            return {"ok": True}
        _starting = body.name
    threading.Thread(target=_switch, args=(body.name, gguf), daemon=True).start()
    return {"ok": True}


def _switch(name: str, gguf) -> None:
    global _starting
    port = urlparse(config.LLM_HOST).port or 8085
    try:
        _kill_port(port)
        # log 導到獨立檔案：llama-server 的輸出量大且格式不同，混進 app.log 會淹掉一切
        out = (config.LOG_DIR / "llama-server.log").open("w", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [str(config.LLAMA_SERVER), "-m", str(gguf), "--port", str(port),
             "--ctx-size", str(config.LLM_CTX_SIZE), "--n-gpu-layers", "999",
             "--jinja", "--temp", "0", "--reasoning", "off"],
            stdout=out, stderr=subprocess.STDOUT,
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


@router.post("/download")
def download_model(body: SelectIn) -> dict:
    entry = next((e for e in CATALOG if e["name"] == body.name), None)
    if entry is None:
        raise HTTPException(404, "型錄裡沒有這顆模型")
    if (config.MODELS_DIR / f"{body.name}.gguf").exists():
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
    part = config.MODELS_DIR / f"{name}.gguf.part"
    try:
        with requests.get(entry["url"], stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with part.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        _downloads[name]["pct"] = min(99, done * 100 // total)
        part.replace(config.MODELS_DIR / f"{name}.gguf")
        del _downloads[name]
        actions.record("下載模型「%s」成功", name)
    except Exception as e:
        log.exception("下載模型失敗 %s", name)
        _downloads[name]["error"] = str(e)
        actions.problem("下載模型「%s」失敗：%s", name, e)
        part.unlink(missing_ok=True)

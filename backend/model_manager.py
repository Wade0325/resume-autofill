"""模型管理：列出、切換、下載 GGUF。

llama-server 一個行程只服務一顆模型，「切換」＝砍掉現有行程、換 gguf 重開。
行程可能是使用者自己開的（開發時跑 ps1），不一定是這裡生的子行程，
所以要砍的對象用 port 去找，不能只記自己的 Popen。

下載與啟動都在背景執行緒進行，呼叫立刻返回；前端輪詢 status() 看進度。
這裡不碰 HTTP——api/models.py 負責把 ModelError 翻成 HTTPException。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from . import actions, config, db
from .core import llm

log = logging.getLogger(__name__)

# 可下載的型錄。官方 Qwen 未提供 GGUF，用 unsloth 的量化版（同 README 第 5 節）。
# 型錄外的模型走 download_url()，貼 Hugging Face 的 .gguf 連結自行下載。
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
]

READY_TIMEOUT = 300    # 9B 冷啟動要載 5 GB 進 VRAM，給足時間
DOWNLOAD_TIMEOUT = (15, 60)
DISK_MARGIN_GB = 0.5   # 除了模型本身，至少要再留這麼多空間（不留的話硬碟正好塞爆）

_lock = threading.Lock()
_starting: str | None = None            # 正在啟動的模型名，None = 沒有
_downloads: dict[str, dict] = {}        # name -> {"pct": int, "error": str|None}
_device: str = ""                       # 跑起來的引擎用 GPU 還是 CPU，空＝不知道
_pid: int | None = None                 # 自己生的 llama-server，用來問它吃了多少 VRAM


class ModelError(Exception):
    """帶 HTTP 狀態碼的操作失敗，訊息直接給使用者看。"""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _mmproj_path(name: str) -> Path:
    return config.MODELS_DIR / f"{name}.mmproj.gguf"


def vision_file_ready(name: str) -> bool:
    """這顆模型的視覺投影檔在不在：在的話啟動時會掛上。模型還沒開也看得出開了之後看不看得到圖。"""
    return _mmproj_path(name).exists()


def _model_path(name: str) -> Path:
    """名稱會接進檔案路徑：只收單純的檔名，「..\\」這種會跑出 models/ 的不收。"""
    if not name or name in (".", "..") or Path(name).name != name:
        raise ModelError(422, "模型名稱不合法")
    return config.MODELS_DIR / f"{name}.gguf"


def status() -> dict:
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
    # list() 先固定住鍵：下載執行緒完成時會 del，邊迭代邊刪會炸
    for name in list(_downloads):      # 自訂網址下載中（或失敗）的也要列
        if not any(r["name"] == name for r in rows):
            rows.append(_row(name, 0, "自訂下載", downloaded=False, downloadable=False))
    # 沒開就不必再探視覺：模型沒開時每探一次要等半秒，前端又一直輪詢這支
    running = llm.available(config.LLM_HOST)
    return {"active": config.LLM_MODEL,
            "running": running,
            "starting": _starting,
            "vision": running and llm.supports_vision(config.LLM_HOST),
            "device": _device if running else "",
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


def select(name: str) -> None:
    global _starting
    gguf = _model_path(name)
    if not gguf.exists():
        raise ModelError(404, "這顆模型還沒下載")
    if not config.LLAMA_SERVER.exists():
        raise ModelError(500, f"找不到 {config.LLAMA_SERVER}，請先取得 llama.cpp（見 README 第 5 節）")
    with _lock:
        if _starting:
            raise ModelError(409, f"「{_starting}」正在啟動中，請稍候")
        # 同一顆模型也可能需要重啟：剛補下載視覺檔時，跑著的引擎還沒掛上它
        vision_ok = _mmproj_path(name).exists() == llm.supports_vision(config.LLM_HOST)
        if name == config.LLM_MODEL and llm.available(config.LLM_HOST) and vision_ok:
            return
        _starting = name
    threading.Thread(target=_switch, args=(name, gguf), daemon=True).start()


def _switch(name: str, gguf: Path) -> None:
    global _starting
    port = urlparse(config.LLM_HOST).port or 8085
    try:
        _kill_port(port)
        # 不指定 --n-gpu-layers：llama.cpp 會自己看剩多少 VRAM 決定放幾層上去。
        # 以前寫死 999，log 直接說「n_gpu_layers already set by user to 999, abort」——
        # 自動配置整個被關掉，顯卡不夠大的機器只能自己改參數。
        # 真的要指定就設 RESUME_AUTOFILL_GPU_LAYERS
        args = [str(config.LLAMA_SERVER), "-m", str(gguf), "--port", str(port),
                "--ctx-size", str(config.LLM_CTX_SIZE),
                "--jinja", "--temp", "0", "--reasoning", "off"]
        if config.GPU_LAYERS:
            args += ["--n-gpu-layers", config.GPU_LAYERS]
        # 視覺投影檔在就掛上，模型才吃得了頁面截圖
        mmproj = _mmproj_path(name)
        if mmproj.exists():
            args += ["--mmproj", str(mmproj)]
        # log 導到獨立檔案：llama-server 的輸出量大且格式不同，混進 app.log 會淹掉一切
        out = (config.LOG_DIR / "llama-server.log").open("w", encoding="utf-8", errors="replace")
        # 金鑰用環境變數給：沒有它，瀏覽器裡的任何網頁都能呼叫這個推論服務。
        # 不用 --api-key-file——llama-server 開不了中文路徑的檔案，程式裝在
        # C:\Users\王小明\ 底下就整個起不來；也不放命令列，別的程式看得到
        global _pid, _device
        proc = subprocess.Popen(
            args, stdout=out, stderr=subprocess.STDOUT,
            env={**os.environ, "LLAMA_API_KEY": llm.ensure_key()},
            creationflags=subprocess.CREATE_NO_WINDOW)
        _pid, _device = proc.pid, ""

        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            if llm.available(config.LLM_HOST):
                config.LLM_MODEL = name
                db.put_kv("llm_model", name)   # 後端重啟後記得這個選擇
                _device = _device_of(proc.pid)
                log.info("模型就緒 %s device=%s", name, _device or "不明")
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


def _device_of(pid: int) -> str:
    """這個 llama-server 吃了多少 VRAM：有就是跑在 GPU 上，沒有就是 CPU。
    問 nvidia-smi 自己生的那個行程，不必去猜 log 怎麼寫（不同版本寫法不一樣）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return "CPU"        # 沒有 nvidia-smi 就是沒有 NVIDIA 顯卡
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 2 and parts[0] == str(pid) and parts[1].isdigit():
            return f"GPU（{int(parts[1]) / 1024:.1f} GB）"
    return "CPU"


def autostart() -> None:
    """開程式時把上次用的模型載回來（以前每次都要自己按「切換」等一兩分鐘）。

    已經有東西在聽那個埠就完全不動作——那可能是使用者自己開的、或別的程式的 server，
    自動啟動不該去砍它。沒下載、找不到 llama-server 也直接跳過。
    """
    name = config.LLM_MODEL
    try:
        if llm.available(config.LLM_HOST):
            log.info("推論埠已經有服務在跑，不自動載入")
            return
        if not _model_path(name).exists() or not config.LLAMA_SERVER.exists():
            return
        log.info("自動載入上次用的模型 %s", name)
        select(name)
    except ModelError as e:
        log.info("不自動載入模型：%s", e)


def delete(name: str) -> None:
    """刪掉模型檔（連同視覺投影檔與沒下載完的暫存檔）。正在用或正在下載的不給刪。"""
    gguf = _model_path(name)
    if name == config.LLM_MODEL and llm.available(config.LLM_HOST):
        raise ModelError(409, "這顆模型正在使用中，請先切換到別顆再刪除")
    active = _downloads.get(name)
    if active and active["error"] is None:
        raise ModelError(409, "這顆模型正在下載中")
    targets = [gguf, _mmproj_path(name),
               _part_of(gguf), _part_of(_mmproj_path(name))]
    removed = 0
    for path in targets:
        if not path.exists():
            continue
        try:
            path.unlink()
        except OSError as e:
            raise ModelError(409, f"刪不掉：{e}") from e
        removed += 1
    if not removed:
        raise ModelError(404, "找不到這顆模型")
    _downloads.pop(name, None)
    log.info("刪除模型 %s（%d 個檔案）", name, removed)
    actions.record("刪除模型「%s」成功", name)


def download(name: str) -> None:
    """下載型錄裡的模型；主檔在、視覺檔缺時只補視覺檔。"""
    entry = next((e for e in CATALOG if e["name"] == name), None)
    if entry is None:
        raise ModelError(404, "型錄裡沒有這顆模型")
    have_main = (config.MODELS_DIR / f"{name}.gguf").exists()
    need_mmproj = "mmproj" in entry and not _mmproj_path(name).exists()
    # 主檔在、視覺檔缺 → 只補視覺檔（早期版本下載的模型沒有 mmproj）
    if have_main and not need_mmproj:
        raise ModelError(409, "這顆模型已經下載過了")
    _check_space(0 if have_main else float(entry.get("size_gb") or 0))
    _begin_download(name, entry)


def download_url(url: str) -> str:
    """自訂模型：貼 Hugging Face 的 .gguf 連結下載到 models/。回傳模型名。

    只做兩件防呆：https、副檔名 .gguf。下載的是資料檔不會被執行，
    這是使用者自己機器上的個人工具，不必更嚴。
    """
    # 檔案頁的 /blob/ 連結幫使用者轉成直接下載的 /resolve/
    url = url.strip().replace("/blob/", "/resolve/", 1)
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ModelError(422, "只接受 https 網址")
    # 先解碼再取檔名：反過來的話 %2F、%5C 在取檔名時還是字面，解碼後才變成斜線，
    # 「..%5C..%5Cx.gguf」就會寫到 models/ 外面去。檔名也只收常見字元
    fname = Path(unquote(parsed.path)).name
    if not fname.lower().endswith(".gguf"):
        raise ModelError(422, "網址必須指向 .gguf 檔（到 Hugging Face 檔案列表複製下載連結）")
    if not re.fullmatch(r"\w[\w.+\-]*\.gguf", fname, flags=re.IGNORECASE):
        raise ModelError(422, "檔名含有不支援的字元")

    name = fname[: -len(".gguf")]
    if (config.MODELS_DIR / fname).exists():
        raise ModelError(409, "已有同名的模型檔")
    _check_space(_remote_size_gb(url))
    _begin_download(name, {"name": name, "url": url})
    return name


def _begin_download(name: str, entry: dict) -> None:
    with _lock:
        active = _downloads.get(name)
        if active and active["error"] is None:
            raise ModelError(409, "已經在下載中")
        _downloads[name] = {"pct": 0, "error": None}
    threading.Thread(target=_download, args=(entry,), daemon=True).start()


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


def _part_of(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".part")


def _check_space(need_gb: float) -> None:
    """空間不夠就別開始——5 GB 下到一半才失敗，時間與流量都白花。"""
    if not need_gb:
        return
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(config.MODELS_DIR).free / 1024 ** 3
    if free_gb < need_gb + DISK_MARGIN_GB:
        raise ModelError(507, f"磁碟空間不夠：這顆模型約 {need_gb:.1f} GB，"
                              f"目前只剩 {free_gb:.1f} GB")


def _remote_size_gb(url: str) -> float:
    """自訂網址：先問對方檔案多大。問不到就回 0（不擋，照下載）。"""
    try:
        r = requests.head(url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
        return int(r.headers.get("Content-Length") or 0) / 1024 ** 3
    except requests.RequestException:
        return 0.0


def _expected_sha(headers) -> str:
    """Hugging Face 的檔案 ETag 就是內容的 sha256，拿來驗下載有沒有壞。
    不是這種格式（一般網站）就不驗。"""
    for key in ("X-Linked-ETag", "ETag"):
        value = (headers.get(key) or "").strip('"')
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(url: str, dest: Path, name: str, track: bool) -> None:
    """下載到 .part 再改名。斷掉時 .part 留著，下次從斷點續傳；
    來源有給校驗碼（Hugging Face 的 ETag 就是 sha256）就驗完才改名。"""
    part = _part_of(dest)
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, headers=headers) as r:
        done_already = have and r.status_code == 416   # 斷點就是檔尾：已經下載完了
        if have and not done_already and r.status_code != 206:
            have = 0                                   # 對方不支援續傳，整個重來
        if not done_already:
            r.raise_for_status()
        expect = _expected_sha(r.headers)
        if have:
            log.info("續傳 %s：已有 %.1f GB", name, have / 1024 ** 3)
        if not done_already:
            total = have + int(r.headers.get("Content-Length") or 0)
            done = have
            with part.open("ab" if have else "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if track and total:
                        _downloads[name]["pct"] = min(99, done * 100 // total)
    if expect and _sha256(part) != expect:
        part.unlink(missing_ok=True)        # 壞檔留著只會一直續傳到同一個壞結果
        raise ModelError(502, "下載的檔案跟來源對不起來（可能中途壞掉），請再試一次")
    part.replace(dest)

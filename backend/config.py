"""路徑與環境變數。"""
from __future__ import annotations

import os
from pathlib import Path

# 所有路徑跟著程式根目錄走（可攜式）：打包版由啟動器用 RESUME_AUTOFILL_ROOT
# 指到解壓根目錄，開發時就是 repo 根。個人資料放 data/——整個資料夾就是
# 完整的程式＋資料，刪掉資料夾＝徹底移除
_ROOT = Path(os.environ.get("RESUME_AUTOFILL_ROOT",
                            Path(__file__).resolve().parent.parent))

def _load_env_file() -> None:
    """開發用的 .env（目前只有 Langfuse 金鑰）。真正的環境變數優先，
    檔案不存在就跳過——不為了這件事引入額外套件。"""
    path = _ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env_file()

HOME = Path(os.environ.get("RESUME_AUTOFILL_HOME", _ROOT / "data"))
DB_PATH = HOME / "app.db"
JOBS_DIR = HOME / "jobs"
LOG_DIR = HOME / "logs"

API_HOST = os.environ.get("RESUME_AUTOFILL_API_HOST", "127.0.0.1")
# 8000/8080 常被開發工具佔走（VS Code 就會），選冷門一點的預設值
API_PORT = int(os.environ.get("RESUME_AUTOFILL_API_PORT", "8090"))

# 寫 IP 不寫 localhost：Windows 上 localhost 先解析成 ::1，llama-server 只聽 127.0.0.1，
# 每次探測都要先等 ::1 那次逾時——模型開著時多 0.5 秒，沒開時整整 1 秒，
# 啟動器的健康檢查因此一直逾時，打包版冷啟動就起不來
LLM_HOST = os.environ.get("RESUME_AUTOFILL_LLM_HOST", "http://127.0.0.1:8085")
LLM_MODEL = os.environ.get("RESUME_AUTOFILL_LLM_MODEL", "Qwen3.5-9B-Q4_K_M")
LLM_CTX_SIZE = int(os.environ.get("RESUME_AUTOFILL_LLM_CTX", "16384"))

MODELS_DIR = Path(os.environ.get("RESUME_AUTOFILL_MODELS_DIR", _ROOT / "models"))
LLAMA_SERVER = Path(os.environ.get("RESUME_AUTOFILL_LLAMA_SERVER", _ROOT / "bin" / "llama-server.exe"))

# 空＝讓 llama.cpp 自己看剩多少 VRAM 決定放幾層（寫死的話它會放棄自動配置）
GPU_LAYERS = os.environ.get("RESUME_AUTOFILL_GPU_LAYERS", "")
# 開程式時自動把上次用的模型載回來；開發時不想等就設 0
AUTOSTART_MODEL = os.environ.get("RESUME_AUTOFILL_AUTOSTART", "1") != "0"

LOG_LEVEL = os.environ.get("RESUME_AUTOFILL_LOG_LEVEL", "INFO")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
JOB_RETENTION_HOURS = 24


def ensure_dirs() -> None:
    for d in (HOME, JOBS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # 履歷含個資，整個目錄限本人存取（Windows 上 chmod 近乎無效，故不視為唯一防線）
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass

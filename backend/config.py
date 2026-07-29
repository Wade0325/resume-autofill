"""路徑與環境變數。"""
from __future__ import annotations

import os
from pathlib import Path

# 所有路徑跟著程式根目錄走（可攜式）：打包版由啟動器用 RESUME_AUTOFILL_ROOT
# 指到解壓根目錄，開發時就是 repo 根。個人資料放 data/——整個資料夾就是
# 完整的程式＋資料，刪掉資料夾＝徹底移除
_ROOT = Path(os.environ.get("RESUME_AUTOFILL_ROOT",
                            Path(__file__).resolve().parent.parent))

HOME = Path(os.environ.get("RESUME_AUTOFILL_HOME", _ROOT / "data"))
DB_PATH = HOME / "app.db"
JOBS_DIR = HOME / "jobs"
LOG_DIR = HOME / "logs"

API_HOST = os.environ.get("RESUME_AUTOFILL_API_HOST", "127.0.0.1")
# 8000/8080 常被開發工具佔走（VS Code 就會），選冷門一點的預設值
API_PORT = int(os.environ.get("RESUME_AUTOFILL_API_PORT", "8090"))

# 推論引擎位置屬於部署設定，與使用者可調的門檻分開（後者存在 DB 的 settings）
LLM_HOST = os.environ.get("RESUME_AUTOFILL_LLM_HOST", "http://localhost:8085")
LLM_MODEL = os.environ.get("RESUME_AUTOFILL_LLM_MODEL", "Qwen3.5-9B-Q4_K_M")
LLM_CTX_SIZE = int(os.environ.get("RESUME_AUTOFILL_LLM_CTX", "16384"))

MODELS_DIR = Path(os.environ.get("RESUME_AUTOFILL_MODELS_DIR", _ROOT / "models"))
LLAMA_SERVER = Path(os.environ.get("RESUME_AUTOFILL_LLAMA_SERVER", _ROOT / "bin" / "llama-server.exe"))

LOG_LEVEL = os.environ.get("RESUME_AUTOFILL_LOG_LEVEL", "INFO")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
JOB_RETENTION_HOURS = 24

DEFAULT_SETTINGS = {
    "min_confidence": 0.60,
    "highlight_filled": True,
}


def ensure_dirs() -> None:
    for d in (HOME, JOBS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # 履歷含個資，整個目錄限本人存取（Windows 上 chmod 近乎無效，故不視為唯一防線）
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass

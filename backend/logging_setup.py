"""log 設定：單行人類可讀格式、request_id 串接、輪替。

隱私規則：**永遠不要把 profile 的值寫進 log**。
這是履歷工具，log 檔很可能被使用者附在問題回報裡送出去。
需要判斷「值有沒有取到」時記長度或筆數，不記內容。
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from contextvars import ContextVar
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)-22s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# str.splitlines() 認得的所有換行字元。日誌頁一行當一筆紀錄解析，訊息裡夾著任何一種，
# 後半段就能假裝成另一筆——任何網頁叫瀏覽器 GET http://127.0.0.1:8090/%0A<偽造的一行>，
# 請求 log 裡的路徑就帶著換行，日誌頁上多出一筆「請到某網址下載更新」
_LINE_BREAKS = {"\n": "\\n", "\r": "\\r", "\v": "\\v", "\f": "\\f", "\x1c": "\\x1c",
                "\x1d": "\\x1d", "\x1e": "\\x1e", "\x85": "\\x85", " ": "\\u2028",
                " ": "\\u2029"}
_ESCAPE = str.maketrans(_LINE_BREAKS)


class OneRecordPerLine(logging.Formatter):
    """一筆紀錄的開頭永遠只佔一行：訊息裡的換行換成看得見的跳脫字。
    例外的 traceback 照舊多行，但後續行一律縮排，不可能被當成新的一筆。"""

    def formatMessage(self, record: logging.LogRecord) -> str:
        record.message = record.message.translate(_ESCAPE)
        return super().formatMessage(record)

    def format(self, record: logging.LogRecord) -> str:
        return "\n  ".join(super().format(record).splitlines())


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = OneRecordPerLine(LOG_FORMAT, datefmt=DATE_FORMAT)
    id_filter = RequestIdFilter()

    # Windows 主控台預設 cp950，中文會變亂碼。檔案 handler 另外指定 utf-8。
    # 凍結成無主控台的 exe 時 sys.stdout 是 None，console handler 直接省略
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    console = None
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        console.addFilter(id_filter)

    # encoding 必須明寫：Windows 的預設是 cp950，中文 log 會直接拋 UnicodeEncodeError
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(id_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    if console is not None:
        root.addHandler(console)
    root.addHandler(file_handler)

    # uvicorn 自帶 handler，不清掉的話 log 檔會同時出現兩種格式。
    # access log 交給我們的 middleware，才帶得到 request_id 與耗時。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    # 開 DEBUG 是為了看自己程式的變數狀態，第三方套件的 DEBUG（watchfiles
    # 每次檔案掃描、urllib3 每個連線）只會把它淹掉，一律壓在 INFO
    for name in ("watchfiles", "urllib3", "requests", "PIL", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.INFO)

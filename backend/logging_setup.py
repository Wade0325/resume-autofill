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


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    id_filter = RequestIdFilter()

    # Windows 主控台預設 cp950，中文會變亂碼。檔案 handler 另外指定 utf-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

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
    root.addHandler(console)
    root.addHandler(file_handler)

    # uvicorn 自帶 handler，不清掉的話 log 檔會同時出現兩種格式。
    # access log 交給我們的 middleware，才帶得到 request_id 與耗時。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

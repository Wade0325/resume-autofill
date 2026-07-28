"""把 log 檔的內容讀出來給前端看。

只回使用者操作留下的紀錄。輪詢與啟動那些背景雜訊會把真正的操作淹掉——
health 每 15 秒一筆、日誌頁本身每 3 秒又一筆。

log 從來不寫入 profile 的值（見 logging_setup），所以攤在畫面上是安全的。
"""
from __future__ import annotations

import logging
import logging.handlers
import re
from typing import List, Optional

from fastapi import APIRouter, Query

from .. import config
from ..schemas import LogEntry

router = APIRouter(tags=["logs"])

# 對應 logging_setup 的格式：時間 等級 [request_id] 模組 訊息
LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) +"
    r"(?P<level>\w+) +"
    r"\[[^\]]*\] +"
    r"(?P<module>\S+) +"
    r"(?P<message>.*)$"
)

TAIL_BYTES = 512 * 1024   # 只讀檔尾，log 會長到 5 MB

STARTUP_MESSAGES = ("服務啟動", "服務關閉", "資料庫就緒", "已清除")
INTERNAL_MODULES = ("uvicorn", "backend.db")


@router.get("/logs", response_model=List[LogEntry])
def read_logs(
    limit: int = Query(300, ge=1, le=2000),
    level: Optional[str] = None,
) -> List[LogEntry]:
    entries = [e for e in _parse(_tail_lines()) if _is_user_action(e)]

    if level:
        wanted = {"ERROR": {"ERROR"},
                  "WARNING": {"ERROR", "WARNING"}}.get(level.upper())
        if wanted:
            entries = [e for e in entries if e.level in wanted]

    entries.reverse()          # 最新的在最前面
    return entries[:limit]


@router.delete("/logs")
def clear_logs() -> dict:
    """清空日誌頁。

    不能直接 truncate：logging 的 handler 開著這個檔，位置指標還停在舊
    偏移，下一筆會把檔案補零成垃圾。改成觸發一次輪替——app.log 從零開始，
    舊內容進 app.log.1，之後回報問題還找得到。
    """
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.acquire()
            try:
                handler.doRollover()
            finally:
                handler.release()
    # 「已清除」在頁面的過濾名單裡，這筆只給開發者對時間點用
    logging.getLogger(__name__).info("已清除日誌頁（輪替 app.log）")
    return {"ok": True}


def _is_user_action(entry: LogEntry) -> bool:
    if entry.module.startswith(INTERNAL_MODULES):
        return False
    if any(m in entry.message for m in STARTUP_MESSAGES):
        return False
    # backend.main 記的是 HTTP 存取——輪詢、靜態檔、換分頁都會產生，
    # 而且是「POST /api/imports → 200」這種開發者語言。
    # 但它同時也記未攔截的例外與失敗的請求，那些要留。
    return not (entry.module == "backend.main" and entry.level == "INFO")


def _tail_lines() -> List[str]:
    path = config.LOG_DIR / "app.log"
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace") as f:
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()       # 丟掉被切一半的那行
        return f.read().splitlines()


def _parse(lines: List[str]) -> List[LogEntry]:
    entries: List[LogEntry] = []
    for line in lines:
        m = LINE_RE.match(line)
        if m:
            entries.append(LogEntry(**m.groupdict()))
        elif entries:
            # 例外的 traceback 是多行的，接在上一筆後面才看得懂
            entries[-1].message += "\n" + line
    return entries

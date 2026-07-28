"""把 log 檔的內容讀出來給前端看。

只回「action」通道（見 actions.py）——那是專門寫給使用者看的白話操作紀錄。
開發者日誌（解析座標、模型判斷、輪詢……）仍在同一個檔案裡，但不上頁面：
訊息是術語，靠關鍵字過濾永遠會漏。

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

ACTION_LOGGER = "action"   # 對應 actions.py 的 logger 名稱


@router.get("/logs", response_model=List[LogEntry])
def read_logs(
    limit: int = Query(300, ge=1, le=2000),
    level: Optional[str] = None,
) -> List[LogEntry]:
    entries = [e for e in _parse(_tail_lines()) if e.module == ACTION_LOGGER]

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
    # 不走 action 通道，所以不會出現在剛清空的頁面上；只給開發者對時間點用
    logging.getLogger(__name__).info("已清除日誌頁（輪替 app.log）")
    return {"ok": True}


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

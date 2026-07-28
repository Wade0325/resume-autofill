"""把 log 檔的內容讀出來給前端看。

log 從來不寫入 profile 的值（見 logging_setup），所以攤在畫面上是安全的。
"""
from __future__ import annotations

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
    r"\[(?P<request_id>[^\]]*)\] +"
    r"(?P<module>\S+) +"
    r"(?P<message>.*)$"
)

TAIL_BYTES = 512 * 1024   # 只讀檔尾，log 會長到 5 MB


@router.get("/logs", response_model=List[LogEntry])
def read_logs(
    limit: int = Query(300, ge=1, le=2000),
    level: Optional[str] = None,
    request_id: Optional[str] = None,
) -> List[LogEntry]:
    entries = _parse(_tail_lines())

    if level:
        wanted = {"ERROR": {"ERROR"},
                  "WARNING": {"ERROR", "WARNING"}}.get(level.upper())
        if wanted:
            entries = [e for e in entries if e.level in wanted]
    if request_id:
        entries = [e for e in entries if e.request_id == request_id]

    entries.reverse()          # 最新的在最前面
    return entries[:limit]


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

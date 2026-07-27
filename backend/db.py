"""SQLite 存取層。用標準庫 sqlite3，不引入 ORM。

profile 與 settings 存成 JSON 放在 kv 表：兩者都是整份讀寫、從不按欄位查詢，
正規化只會換來 join 與 migration 成本，而 matcher.get_value() 本來就吃巢狀 dict。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS template (
    fingerprint TEXT PRIMARY KEY,
    source_name TEXT NOT NULL DEFAULT '',
    mapping     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status      TEXT NOT NULL,
    anchors     TEXT NOT NULL,
    decided     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
    log.info("資料庫就緒 path=%s", config.DB_PATH)


# ---------------- kv：profile / settings ----------------
def get_kv(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def put_kv(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now()))


def get_settings() -> Dict[str, Any]:
    stored = get_kv("settings") or {}
    return {**config.DEFAULT_SETTINGS, **stored}


# ---------------- template：範本學習成果 ----------------
def get_template(fingerprint: str) -> Dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT mapping FROM template WHERE fingerprint = ?",
                           (fingerprint,)).fetchone()
    return json.loads(row["mapping"]) if row else {}


def put_template(fingerprint: str, mapping: Dict[str, str], source_name: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO template (fingerprint, source_name, mapping, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(fingerprint) DO UPDATE SET "
            "mapping = excluded.mapping, source_name = excluded.source_name, "
            "updated_at = excluded.updated_at",
            (fingerprint, source_name, json.dumps(mapping, ensure_ascii=False), _now()))


# ---------------- job：一次上傳的處理過程 ----------------
def create_job(job_id: str, filename: str, fingerprint: str,
               anchors: List[Dict[str, Any]], decided: Dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job (id, filename, fingerprint, status, anchors, decided, "
            "created_at) VALUES (?, ?, ?, 'analyzed', ?, ?, ?)",
            (job_id, filename, fingerprint,
             json.dumps(anchors, ensure_ascii=False),
             json.dumps(decided, ensure_ascii=False), _now()))


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    job["anchors"] = json.loads(job["anchors"])
    job["decided"] = json.loads(job["decided"])
    return job


def update_job(job_id: str, *, decided: Optional[Dict[str, Any]] = None,
               status: Optional[str] = None) -> None:
    sets, params = [], []
    if decided is not None:
        sets.append("decided = ?")
        params.append(json.dumps(decided, ensure_ascii=False))
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    params.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE job SET {', '.join(sets)} WHERE id = ?", params)


def delete_job(job_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM job WHERE id = ?", (job_id,))


def purge_old_jobs(hours: int = config.JOB_RETENTION_HOURS) -> int:
    """啟動時清掉過期的上傳檔。履歷是個資，不該無限期留在磁碟上。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute("SELECT id FROM job WHERE created_at < ?", (cutoff,)).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.executemany("DELETE FROM job WHERE id = ?", [(i,) for i in ids])
    for jid in ids:
        _rmtree(config.JOBS_DIR / jid)
    return len(ids)


def _rmtree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        child.unlink(missing_ok=True)
    path.rmdir()

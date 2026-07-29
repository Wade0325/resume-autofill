"""SQLite 存取層。用標準庫 sqlite3，不引入 ORM。

profile 與 settings 存成 JSON 放在 kv 表：兩者都是整份讀寫、從不按欄位查詢，
正規化只會換來 join 與 migration 成本，而 planner.get_value() 本來就吃巢狀 dict。
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
    status      TEXT NOT NULL,   -- processing | analyzed | written | failed
    anchors     TEXT NOT NULL,
    decided     TEXT NOT NULL,
    stage       TEXT NOT NULL DEFAULT '',   -- processing 時目前進行到哪一步
    error       TEXT NOT NULL DEFAULT '',   -- failed 時給使用者看的原因
    created_at  TEXT NOT NULL
);
-- 匯入與填寫存的東西已經不一樣了：填寫要 anchor 座標才寫得回去，
-- 匯入只要模型讀出來的值。與其把 job 塞成兩用，不如分開。
CREATE TABLE IF NOT EXISTS import_job (
    id         TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    extracted  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ready',   -- processing | ready | failed
    stage      TEXT NOT NULL DEFAULT '',        -- processing 時目前進行到哪一步
    error      TEXT NOT NULL DEFAULT '',        -- failed 時給使用者看的原因
    created_at TEXT NOT NULL
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
        # 既有資料庫補欄位（SQLite 的 IF NOT EXISTS 不會改舊表）。
        # import_job 的 status 預設 ready：舊資料列都是同步時代分析完才寫入的
        for ddl in ("ALTER TABLE job ADD COLUMN stage TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE job ADD COLUMN error TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE import_job ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'",
                    "ALTER TABLE import_job ADD COLUMN stage TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE import_job ADD COLUMN error TEXT NOT NULL DEFAULT ''"):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass   # 欄位已存在
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
def create_job(job_id: str, filename: str, fingerprint: str = "",
               anchors: Optional[List[Dict[str, Any]]] = None,
               decided: Optional[Dict[str, Any]] = None,
               status: str = "analyzed") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job (id, filename, fingerprint, status, anchors, decided, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, filename, fingerprint, status,
             json.dumps(anchors or [], ensure_ascii=False),
             json.dumps(decided or {}, ensure_ascii=False), _now()))


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
               status: Optional[str] = None,
               anchors: Optional[List[Dict[str, Any]]] = None,
               fingerprint: Optional[str] = None,
               stage: Optional[str] = None,
               error: Optional[str] = None) -> None:
    sets, params = [], []
    if decided is not None:
        sets.append("decided = ?")
        params.append(json.dumps(decided, ensure_ascii=False))
    if anchors is not None:
        sets.append("anchors = ?")
        params.append(json.dumps(anchors, ensure_ascii=False))
    if fingerprint is not None:
        sets.append("fingerprint = ?")
        params.append(fingerprint)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if stage is not None:
        sets.append("stage = ?")
        params.append(stage)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if not sets:
        return
    params.append(job_id)
    with connect() as conn:
        conn.execute(f"UPDATE job SET {', '.join(sets)} WHERE id = ?", params)


def fail_stale_jobs() -> int:
    """把上次關機時還在分析中的工作標成失敗——執行緒已經死了，不會有結果。"""
    n = 0
    with connect() as conn:
        for table in ("job", "import_job"):
            n += conn.execute(
                f"UPDATE {table} SET status = 'failed', "
                "error = '分析被伺服器重啟中斷，請重新上傳' WHERE status = 'processing'").rowcount
    return n


# ---------------- import_job：一次匯入讀到的資料 ----------------
def create_import(import_id: str, filename: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO import_job (id, filename, extracted, status, created_at) "
            "VALUES (?, ?, '{}', 'processing', ?)",
            (import_id, filename, _now()))


def update_import(import_id: str, *, extracted: Optional[Dict[str, Any]] = None,
                  status: Optional[str] = None, stage: Optional[str] = None,
                  error: Optional[str] = None) -> None:
    sets, params = [], []
    if extracted is not None:
        sets.append("extracted = ?")
        params.append(json.dumps(extracted, ensure_ascii=False))
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if stage is not None:
        sets.append("stage = ?")
        params.append(stage)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if not sets:
        return
    params.append(import_id)
    with connect() as conn:
        conn.execute(f"UPDATE import_job SET {', '.join(sets)} WHERE id = ?", params)


def get_import(import_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM import_job WHERE id = ?", (import_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["extracted"] = json.loads(out["extracted"])
    return out


def purge_old_jobs(hours: int = config.JOB_RETENTION_HOURS) -> int:
    """啟動時清掉過期的上傳檔。履歷是個資，不該無限期留在磁碟上。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as conn:
        ids = [r["id"] for table in ("job", "import_job")
               for r in conn.execute(
                   f"SELECT id FROM {table} WHERE created_at < ?", (cutoff,)).fetchall()]
        for table in ("job", "import_job"):
            conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
    for jid in ids:
        _rmtree(config.JOBS_DIR / jid)
    return len(ids)


def _rmtree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        child.unlink(missing_ok=True)
    path.rmdir()

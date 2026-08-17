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
    source_name TEXT NOT NULL DEFAULT '',   -- 純診斷用：這份範本從哪個檔名學來，程式不讀
    mapping     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status      TEXT NOT NULL,   -- processing | analyzed | failed
    anchors     TEXT NOT NULL,
    decided     TEXT NOT NULL,
    form_fields TEXT NOT NULL DEFAULT '[]', -- VLM 看版面認出「這份表格要填哪些欄位」
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
                    "ALTER TABLE job ADD COLUMN form_fields TEXT NOT NULL DEFAULT '[]'",
                    "ALTER TABLE import_job ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'",
                    "ALTER TABLE import_job ADD COLUMN stage TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE import_job ADD COLUMN error TEXT NOT NULL DEFAULT ''"):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass   # 欄位已存在
    log.info("資料庫就緒 path=%s", config.DB_PATH)


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


# job 與 import_job 的存取共用同一套「SELECT * → dict → 解 JSON 欄位」與
# 動態 SET 樣板，只差表名與哪些欄位是 JSON
_JSON_COLS = {"job": ("anchors", "decided", "form_fields"),
              "import_job": ("extracted",)}


def _get_row(table: str, row_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    for col in _JSON_COLS[table]:
        out[col] = json.loads(out[col])
    return out


def _update_row(table: str, row_id: str, fields: Dict[str, Any]) -> None:
    """None 的欄位代表這次不更新。"""
    sets, params = [], []
    for col, value in fields.items():
        if value is None:
            continue
        sets.append(f"{col} = ?")
        params.append(json.dumps(value, ensure_ascii=False)
                      if col in _JSON_COLS[table] else value)
    if not sets:
        return
    params.append(row_id)
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?", params)


def create_job(job_id: str, filename: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job (id, filename, fingerprint, status, anchors, decided, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, filename, "", status, "[]", "{}", _now()))


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _get_row("job", job_id)


def update_job(job_id: str, *, decided: Optional[Dict[str, Any]] = None,
               status: Optional[str] = None,
               anchors: Optional[List[Dict[str, Any]]] = None,
               fingerprint: Optional[str] = None,
               form_fields: Optional[List[str]] = None,
               stage: Optional[str] = None,
               error: Optional[str] = None) -> None:
    _update_row("job", job_id, {"decided": decided, "anchors": anchors,
                                "fingerprint": fingerprint, "status": status,
                                "form_fields": form_fields,
                                "stage": stage, "error": error})


def fail_stale_jobs() -> int:
    """把上次關機時還在分析中的工作標成失敗——執行緒已經死了，不會有結果。"""
    n = 0
    with connect() as conn:
        for table in ("job", "import_job"):
            n += conn.execute(
                f"UPDATE {table} SET status = 'failed', "
                "error = '分析被伺服器重啟中斷，請重新上傳' WHERE status = 'processing'").rowcount
    return n


def create_import(import_id: str, filename: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO import_job (id, filename, extracted, status, created_at) "
            "VALUES (?, ?, '{}', 'processing', ?)",
            (import_id, filename, _now()))


def update_import(import_id: str, *, extracted: Optional[Dict[str, Any]] = None,
                  status: Optional[str] = None, stage: Optional[str] = None,
                  error: Optional[str] = None) -> None:
    _update_row("import_job", import_id, {"extracted": extracted, "status": status,
                                          "stage": stage, "error": error})


def get_import(import_id: str) -> Optional[Dict[str, Any]]:
    return _get_row("import_job", import_id)


def purge_old_jobs(hours: int = config.JOB_RETENTION_HOURS) -> int:
    """啟動時清掉過期的上傳檔。履歷是個資，不該無限期留在磁碟上。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as conn:
        ids = [r["id"] for table in ("job", "import_job")
               for r in conn.execute(
                   f"SELECT id FROM {table} WHERE created_at < ?", (cutoff,)).fetchall()]
        for table in ("job", "import_job"):
            conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
        alive = {r["id"] for table in ("job", "import_job")
                 for r in conn.execute(f"SELECT id FROM {table}").fetchall()}
    for jid in ids:
        _rmtree(config.JOBS_DIR / jid)

    # 資料庫查無此人的孤兒資料夾也要掃：紀錄刪了但當時資料夾沒刪成
    # （檔案被轉檔鎖住之類），之後就再也不會被看見，會永久殘留
    orphans = 0
    if config.JOBS_DIR.exists():
        for path in config.JOBS_DIR.iterdir():
            if path.is_dir() and path.name not in alive:
                _rmtree(path)
                orphans += not path.exists()
    return len(ids) + orphans


def _rmtree(path: Path) -> None:
    """刪不掉就留著（檔案還被鎖住），下次啟動的孤兒掃描會再試。"""
    try:
        for child in path.iterdir():
            child.unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        pass

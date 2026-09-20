"""SQLite 存取層。用標準庫 sqlite3，不引入 ORM。

profile 與 settings 存成 JSON 放在 kv 表：兩者都是整份讀寫、從不按欄位查詢，
正規化只會換來 join 與 migration 成本，而 planner.get_value() 本來就吃巢狀 dict。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from . import config

log = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 600     # 孤兒資料夾要放超過 10 分鐘才清，見 purge_old_jobs

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
    engine      TEXT NOT NULL DEFAULT 'classic', -- 這份是哪一條路填的：classic | vlm
    apply       TEXT NOT NULL DEFAULT '{}',   -- 「這次應徵」：應徵職務、工作地點…只算這一份
    typed       TEXT NOT NULL DEFAULT '{}',   -- 使用者在對映清單自己打的值：{位置代碼: 字}
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
    note       TEXT NOT NULL DEFAULT '',        -- 給使用者的提醒（掃描檔沒有原文可比對…）
    created_at TEXT NOT NULL
);
-- 我的資料被換掉之前的樣子：存檔、匯入、還原前各留一份，只留最近 KEEP_VERSIONS 份
CREATE TABLE IF NOT EXISTS profile_version (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    value      TEXT NOT NULL,
    reason     TEXT NOT NULL,   -- 被什麼換掉：save | import | restore | file
    created_at TEXT NOT NULL
);
"""

KEEP_VERSIONS = 30


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


def _add_column(conn: sqlite3.Connection, table: str, column: str) -> None:
    """已經有這一欄就不動。以前是加了再吞掉錯誤，連「資料庫被鎖住」都一起吞了。"""
    name = column.split()[0]
    if name not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")


def _v4(conn: sqlite3.Connection) -> None:
    # 匯入的提醒（掃描檔）
    _add_column(conn, "import_job", "note TEXT NOT NULL DEFAULT ''")


def _v3(conn: sqlite3.Connection) -> None:
    # 對映清單可以直接把某一格改成自己打的字
    _add_column(conn, "job", "typed TEXT NOT NULL DEFAULT '{}'")


def _v2(conn: sqlite3.Connection) -> None:
    # 「這次應徵」：每間公司不一樣的欄位跟著這份工作走，不進「我的資料」
    _add_column(conn, "job", "apply TEXT NOT NULL DEFAULT '{}'")


def _v1(conn: sqlite3.Connection) -> None:
    # 舊資料庫補欄位（SCHEMA 的 IF NOT EXISTS 不會改舊表）。
    # import_job 的 status 預設 ready：舊資料列都是同步時代分析完才寫入的
    for table, column in (("job", "stage TEXT NOT NULL DEFAULT ''"),
                          ("job", "error TEXT NOT NULL DEFAULT ''"),
                          ("job", "form_fields TEXT NOT NULL DEFAULT '[]'"),
                          ("job", "engine TEXT NOT NULL DEFAULT 'classic'"),
                          ("import_job", "status TEXT NOT NULL DEFAULT 'ready'"),
                          ("import_job", "stage TEXT NOT NULL DEFAULT ''"),
                          ("import_job", "error TEXT NOT NULL DEFAULT ''")):
        _add_column(conn, table, column)


# 資料庫版本記在 PRAGMA user_version：第 n 步做完就記成 n，下次從沒做過的那步接著做。
# 新表寫在 SCHEMA 就好；改舊表（加欄位、搬資料）才要在這裡加一步，已經發出去的步驟不要改
MIGRATIONS = [_v1, _v2, _v3, _v4]


def init() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            log.warning("資料庫版本 %d 比程式認得的 %d 新，可能是新版程式建的", version, len(MIGRATIONS))
        for n, step in enumerate(MIGRATIONS[version:], start=version + 1):
            step(conn)
            conn.execute(f"PRAGMA user_version = {n}")
            log.info("資料庫升級到第 %d 版", n)
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


def put_profile(value: Dict[str, Any], reason: str) -> bool:
    """寫入我的資料，被換掉的那份先留一個版本；回傳有沒有留。
    讀舊值、留版本、寫新值在同一個交易裡：兩邊同時存檔時不會漏留或留錯"""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM kv WHERE key = 'profile'").fetchone()
        old = json.loads(row["value"]) if row else None
        last = conn.execute(
            "SELECT value FROM profile_version ORDER BY id DESC LIMIT 1").fetchone()
        # 空的、沒變的、跟最近一版一樣的都不必再留
        kept = bool(old) and old != value and (not last or json.loads(last["value"]) != old)
        if kept:
            conn.execute(
                "INSERT INTO profile_version (value, reason, created_at) VALUES (?, ?, ?)",
                (json.dumps(old, ensure_ascii=False), reason, _now()))
            conn.execute(
                "DELETE FROM profile_version WHERE id NOT IN "
                "(SELECT id FROM profile_version ORDER BY id DESC LIMIT ?)", (KEEP_VERSIONS,))
        conn.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES ('profile', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (json.dumps(value, ensure_ascii=False), _now()))
    return kept


def list_profile_versions() -> List[Dict[str, Any]]:
    """新的在前。"""
    with connect() as conn:
        rows = conn.execute("SELECT id, value, reason, created_at FROM profile_version "
                            "ORDER BY id DESC").fetchall()
    return [{**dict(r), "value": json.loads(r["value"])} for r in rows]


def get_profile_version(version_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM profile_version WHERE id = ?",
                           (version_id,)).fetchone()
    return json.loads(row["value"]) if row else None


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


def list_templates() -> List[Dict[str, Any]]:
    """學過的格式：新的在前。mapping 只回筆數，內容是對映細節，管理介面用不到。"""
    with connect() as conn:
        rows = conn.execute("SELECT fingerprint, source_name, mapping, updated_at "
                            "FROM template ORDER BY updated_at DESC").fetchall()
    return [{"fingerprint": r["fingerprint"], "source_name": r["source_name"],
             "slots": len(json.loads(r["mapping"])), "updated_at": r["updated_at"]}
            for r in rows]


def rekey_template(old: str, new: str) -> None:
    """把一份學過的格式換個鍵（舊資料只有結構指紋，用到時搬到「結構＋欄名」底下）。"""
    with connect() as conn:
        conn.execute("UPDATE OR REPLACE template SET fingerprint = ? WHERE fingerprint = ?",
                     (new, old))


def delete_template(fingerprint: str) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM template WHERE fingerprint = ?",
                            (fingerprint,)).rowcount > 0


# job 與 import_job 的存取共用同一套「SELECT * → dict → 解 JSON 欄位」與
# 動態 SET 樣板，只差表名與哪些欄位是 JSON
_JSON_COLS = {"job": ("anchors", "decided", "form_fields", "apply", "typed"),
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


def create_job(job_id: str, filename: str, status: str, engine: str = "classic") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO job (id, filename, fingerprint, status, anchors, decided, "
            "engine, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, filename, "", status, "[]", "{}", engine, _now()))


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _get_row("job", job_id)


def update_job(job_id: str, *, decided: Optional[Dict[str, Any]] = None,
               status: Optional[str] = None,
               anchors: Optional[List[Dict[str, Any]]] = None,
               fingerprint: Optional[str] = None,
               form_fields: Optional[List[str]] = None,
               stage: Optional[str] = None,
               error: Optional[str] = None,
               apply: Optional[Dict[str, str]] = None,
               typed: Optional[Dict[str, str]] = None) -> None:
    _update_row("job", job_id, {"decided": decided, "anchors": anchors,
                                "fingerprint": fingerprint, "status": status,
                                "form_fields": form_fields,
                                "stage": stage, "error": error, "apply": apply,
                                "typed": typed})


def list_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    """最近填過的（新的在前）。過期的上傳檔連同紀錄會被清掉，所以這裡看到的都還在保留期內。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, filename, status, engine, error, created_at FROM job "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


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
                  error: Optional[str] = None, note: Optional[str] = None) -> None:
    _update_row("import_job", import_id, {"extracted": extracted, "status": status,
                                          "stage": stage, "error": error, "note": note})


def get_import(import_id: str) -> Optional[Dict[str, Any]]:
    return _get_row("import_job", import_id)


def purge_old_jobs(hours: int = config.JOB_RETENTION_HOURS) -> int:
    """清掉過期的上傳檔（啟動時一次，之後每小時一次）。履歷是個資，不該無限期留在磁碟上。"""
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
    # （檔案被轉檔鎖住之類），之後就再也不會被看見，會永久殘留。
    # 剛建立的不算：上傳是先存檔再寫資料庫，清理剛好落在兩步中間會把新上傳的刪掉
    orphans = 0
    young = time.time() - ORPHAN_GRACE_SECONDS
    if config.JOBS_DIR.exists():
        for path in config.JOBS_DIR.iterdir():
            if path.is_dir() and path.name not in alive and path.stat().st_mtime < young:
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

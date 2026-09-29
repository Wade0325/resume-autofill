"""我的資料的存檔、版本紀錄與備份檔。

每次寫入（存檔、匯入履歷、還原）前，被換掉的那份都會留一個版本，改壞了找得回來。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import actions, db
from .core.schema import BY_KEY, FIELDS, PER_JOB_KEYS

log = logging.getLogger(__name__)

FORMAT = "resume-autofill-profile"     # 備份檔自己的標記，還原時認這個


def _shapes() -> Dict[str, type]:
    """最上層每一區的形狀：學經歷這種多筆的是清單，自傳是一段字，其他是一組欄位。"""
    out: Dict[str, type] = {}
    for f in FIELDS:
        if "[]." in f.key:
            out.setdefault(f.key.split("[].")[0], list)
        elif "." in f.key:
            out.setdefault(f.key.split(".")[0], dict)
        else:
            out.setdefault(f.key, str)
    return out


SHAPES = _shapes()


def problem(profile: Any) -> Optional[str]:
    """結構不對就回一句原因，對就回 None。
    只看認得的區塊；不認得的照留，新版程式存的東西不該被舊版擋掉"""
    if not isinstance(profile, dict):
        return "內容不是一份資料"
    for root, shape in SHAPES.items():
        value = profile.get(root)
        if value is None:
            continue
        if shape is list:
            if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
                return f"「{root}」應該是一筆一筆的清單"
        elif not isinstance(value, shape):
            return f"「{root}」的格式不對"
    # 日期要是字（整數年份存檔時會自動轉）。小數不收：「2018.10」讀進來已經是 2018.1，
    # 存下去就變成一月，填進表格也是錯的
    for key, value in _date_values(profile):
        if value is not None and not isinstance(value, str) and (
                isinstance(value, bool) or not isinstance(value, int)):
            return f"「{BY_KEY[key].label}」的日期要用文字寫，例如 2018年10月"
    return None


def _date_values(profile: Dict[str, Any]):
    """整份資料裡 schema 標成日期的 (欄位代碼, 值)。"""
    for root, value in profile.items():
        rows = value if isinstance(value, list) else [value]
        for row in rows:
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                key = f"{root}[].{k}" if isinstance(value, list) else f"{root}.{k}"
                if key in _DATE_KEYS:
                    yield key, v


# 日期寫法百百種：手選的是「1996年04月15日」，匯入的常是「1996/4/15」「1996-04-15」。
# 存成同一種，我的資料頁的年月日下拉才認得（認不得就退化成文字框）
_DATE_RE = re.compile(r"\s*(\d{2,4})\s*[年/.\-]\s*(\d{1,2})"
                      r"(?:\s*[月/.\-]\s*(\d{1,2}))?\s*日?\s*$")
_DATE_KEYS = {k for k, f in BY_KEY.items() if f.kind == "date"}


def _one_date(value: Any) -> Any:
    """認得出年月（日）就寫成同一種；「至今」「民國85年」這種認不出來的原樣保留。
    不換算曆制：存的是民國年就還是民國年，由 basic.birthday_era 決定怎麼解讀。

    手改的備份或直接呼叫 API 可能把年份存成數字：我的資料頁與填寫都只認字，數字
    會讓我的資料頁整頁當掉（連還原的按鈕都按不到）。整數轉成字；小數存檔前就擋掉了
    （見 problem），舊資料裡的原樣轉成字、不當日期整理，看得出是怪值才會去改"""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return value
    m = _DATE_RE.fullmatch(value)
    if not m:
        return value
    year, month, day = m.groups()
    return f"{year}年{int(month):02d}月" + (f"{int(day):02d}日" if day else "")


def normalize_dates(profile: Dict[str, Any]) -> Dict[str, Any]:
    """整份資料裡的日期欄位統一寫法。只動 schema 標成日期的欄位。"""
    out = dict(profile)
    for root, value in profile.items():
        if isinstance(value, dict):
            out[root] = {k: (_one_date(v) if f"{root}.{k}" in _DATE_KEYS else v)
                         for k, v in value.items()}
        elif isinstance(value, list):
            out[root] = [{k: (_one_date(v) if f"{root}[].{k}" in _DATE_KEYS else v)
                          for k, v in row.items()} if isinstance(row, dict) else row
                         for row in value]
    return out


def _without_per_job(profile: Dict[str, Any]) -> Dict[str, Any]:
    """應徵職務、工作地點只算那一份工作（「這次應徵」），不進我的資料。以前匯入會誤存進去，
    我的資料頁看不到也刪不掉，看版面那條路還會把它列給模型挑；我的資料頁存檔時又會
    原封不動送回來，備份檔也帶著走。"""
    job = profile.get("job")
    if not isinstance(job, dict):
        return profile
    kept = {k: v for k, v in job.items() if f"job.{k}" not in PER_JOB_KEYS}
    return profile if len(kept) == len(job) else {**profile, "job": kept}


def clean(profile: Dict[str, Any]) -> Dict[str, Any]:
    """存進我的資料之前整理一次：日期統一寫法、拿掉只算那一份工作的欄位。
    所有寫入（存檔、匯入、還原版本、從檔案還原）都走 save，升級時也拿它整理舊資料（db._v5）。"""
    return _without_per_job(normalize_dates(profile))


def save(profile: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """reason 是被什麼換掉：save | import | restore | file，版本紀錄照這個顯示。
    回傳實際存進去的那一份（整理過的）。"""
    profile = clean(profile)
    kept = db.put_profile(profile, reason)
    # 只記結構規模，不記內容——profile 裡全是個資
    log.info("我的資料已更新 原因=%s 留版本=%s 區塊=%d", reason, kept, len(profile))
    return profile


def _flatten(value: Any, path: str = "") -> Dict[str, str]:
    if isinstance(value, dict):
        return {k: v for key, sub in value.items()
                for k, v in _flatten(sub, f"{path}.{key}" if path else key).items()}
    if isinstance(value, list) and any(isinstance(x, dict) for x in value):
        return {k: v for i, sub in enumerate(value)
                for k, v in _flatten(sub, f"{path}[{i}]").items()}
    text = ("、".join(map(str, value)) if isinstance(value, list)
            else str(value if value is not None else ""))
    return {path: text.strip()} if text.strip() else {}


def versions() -> List[Dict[str, Any]]:
    """新的在前；changed＝跟現在相比有幾個欄位不一樣，挑版本時看得出差多少。"""
    now = _flatten(db.get_kv("profile") or {})
    out = []
    for v in db.list_profile_versions():
        old = _flatten(v["value"])
        out.append({"id": v["id"], "reason": v["reason"], "created_at": v["created_at"],
                    "changed": sum(now.get(k) != old.get(k) for k in now.keys() | old.keys())})
    return out


def restore_version(version_id: int) -> Optional[Dict[str, Any]]:
    value = db.get_profile_version(version_id)
    if value is None:
        return None
    saved = save(value, "restore")
    actions.record("還原我的資料成功")
    return saved


def export() -> Dict[str, Any]:
    return {"format": FORMAT, "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "profile": db.get_kv("profile") or {}}


def restore_file(payload: Any) -> Dict[str, Any]:
    """還原備份檔：認得自己匯出的格式，也收直接一份我的資料。

    不對就丟 ValueError（給人看的原因）。
    """
    ours = isinstance(payload, dict) and payload.get("format") == FORMAT
    profile = payload.get("profile") if ours else payload
    why = problem(profile)
    if why is None and not ours and not any(key in SHAPES for key in profile):
        why = "檔案裡沒有我的資料"      # 隨便一個 JSON（例如設定檔）不能蓋掉我的資料
    if why:
        raise ValueError(why)
    saved = save(profile, "file")
    actions.record("從檔案還原我的資料成功")
    return saved

"""我的資料的存檔、版本紀錄與備份檔。

每次寫入（存檔、匯入履歷、還原）前，被換掉的那份都會留一個版本，改壞了找得回來。
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import actions, db
from .core.schema import BY_KEY, FIELDS

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
    return None


# 日期寫法百百種：手選的是「1996年04月15日」，匯入的常是「1996/4/15」「1996-04-15」。
# 存成同一種，我的資料頁的年月日下拉才認得（認不得就退化成文字框）
_DATE_RE = re.compile(r"\s*(\d{2,4})\s*[年/.\-]\s*(\d{1,2})"
                      r"(?:\s*[月/.\-]\s*(\d{1,2}))?\s*日?\s*$")
_DATE_KEYS = {k for k, f in BY_KEY.items() if f.kind == "date"}


def _one_date(value: Any) -> Any:
    """認得出年月（日）就寫成同一種；「至今」「民國85年」這種認不出來的原樣保留。
    不換算曆制：存的是民國年就還是民國年，由 basic.birthday_era 決定怎麼解讀。"""
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


def save(profile: Dict[str, Any], reason: str) -> None:
    """reason 是被什麼換掉：save | import | restore | file，版本紀錄照這個顯示。"""
    profile = normalize_dates(profile)
    kept = db.put_profile(profile, reason)
    # 只記結構規模，不記內容——profile 裡全是個資
    log.info("我的資料已更新 原因=%s 留版本=%s 區塊=%d", reason, kept, len(profile))


def _flatten(value: Any, path: str = "") -> Dict[str, str]:
    if isinstance(value, dict):
        return {k: v for key, sub in value.items()
                for k, v in _flatten(sub, f"{path}.{key}" if path else key).items()}
    if isinstance(value, list) and any(isinstance(x, dict) for x in value):
        return {k: v for i, sub in enumerate(value) for k, v in _flatten(sub, f"{path}[{i}]").items()}
    text = "、".join(map(str, value)) if isinstance(value, list) else str(value if value is not None else "")
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
    save(value, "restore")
    actions.record("還原我的資料成功")
    return value


def export() -> Dict[str, Any]:
    """備份檔。大頭照一起帶（base64）——少了它，換一台電腦還得自己補一次。"""
    from . import service
    out = {"format": FORMAT, "version": 1,
           "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "profile": db.get_kv("profile") or {}}
    if service.photo_path().exists():
        out["photo"] = base64.b64encode(service.photo_path().read_bytes()).decode()
    return out


def restore_file(payload: Any) -> Dict[str, Any]:
    """還原備份檔：認得自己匯出的格式，也收直接一份我的資料。不對就丟 ValueError（給人看的原因）。"""
    ours = isinstance(payload, dict) and payload.get("format") == FORMAT
    profile = payload.get("profile") if ours else payload
    why = problem(profile)
    if why is None and not ours and not any(key in SHAPES for key in profile):
        why = "檔案裡沒有我的資料"      # 隨便一個 JSON（例如設定檔）不能蓋掉我的資料
    if why:
        raise ValueError(why)
    save(profile, "file")
    # 備份檔裡有照片就一起還原；沒有的話保留目前這張，不要默默刪掉人家的
    if ours and payload.get("photo"):
        from . import service
        try:
            service.save_photo(base64.b64decode(payload["photo"]))
        except Exception:
            log.warning("備份檔裡的照片還原失敗，其他資料照樣還原了")
    actions.record("從檔案還原我的資料成功")
    return profile

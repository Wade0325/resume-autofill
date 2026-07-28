"""決定履歷表上每個位置該填什麼，並產生填寫計畫。

模型拿到的是整份文件的全文，可填位置以 {{id}} 標記在原地，
所以它是憑上下文判斷，而不是靠某一格旁邊的字。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import llm
from .document import Slot
from .schema import BY_KEY, FIELD_KEYS, describe_fields

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是履歷表單的欄位對映器。使用者會給你一份履歷表全文，
文中的 {{id}} 是可以填字的位置。請為每個 id 指出應該填入哪個欄位。

規則：
1. 只能使用給定的欄位代碼。
2. **位置上已經印著欄位名稱的，那是表格印好的標籤，一律 __SKIP__。**
   「{{a}}姓　名 | {{b}}」中 a 是標籤要 __SKIP__，b 才是填姓名的地方。
   「{{c}}王小明」的 c 印的是人名不是欄位名稱，可以填。
3. id 的格式是 tblT.rR.cC，R 是列號、C 是欄號，都從 0 開始。
4. **表頭整列都要 __SKIP__，不能只跳過第一格。**
   「{{tbl1.r0.c0}}學校名稱 | {{tbl1.r0.c1}}科系 | {{tbl1.r0.c2}}學位」
   這三格印的都是欄位名稱，r0 整列 __SKIP__；要填的是 r1、r2 那幾列。
5. 不是求職者要填的位置（說明文字、公司內部欄位如面試評語、建議薪資、
   主管簽名）一律選 __SKIP__。
6. 確定是求職者要填、但清單裡沒有對應欄位，選 __UNKNOWN__。
7. 勾選題照樣給對應欄位，系統會自己決定勾哪一個選項。
8. ordinal 一律填 0，系統會自己排出是第幾筆。
9. label 請照抄該位置旁邊印在表格上的字，讓使用者認得出是哪一格。
10. confidence 是 0.0~1.0 的信心值，不確定就給低分。
11. 每個 id 只輸出一次，且必須輸出全部的 id。"""


@dataclass
class FillOp:
    slot: Slot
    field_key: str
    value: str
    confidence: float
    source: str               # cache | model | manual
    label: str = ""           # 表格上印在這格旁邊的字，由模型抄回來
    note: str = ""
    ordinal: int = 0


Decision = Tuple[str, int, float, str, str]   # field_key, ordinal, confidence, source, label


# --------------------------------------------------------------------------
# 決定對映
# --------------------------------------------------------------------------
def decide(text: str, slots: List[Slot], host: str, model: str,
           cached: Optional[Dict[str, Any]] = None) -> Dict[str, Decision]:
    cached = cached or {}
    decisions: Dict[str, Decision] = {}
    pending = []

    for slot in slots:
        hit = cached.get(slot.id)
        if hit:
            decisions[slot.id] = (hit["field_key"], hit.get("ordinal", 0), 1.0,
                                  "cache", hit.get("label", ""))
        else:
            pending.append(slot)

    log.info("快取命中 %d 格，待判斷 %d 格", len(decisions), len(pending))
    if not pending:
        return decisions

    ids = [s.id for s in pending]
    result = llm.ask(host, SYSTEM_PROMPT, _prompt(text, pending),
                     _schema(ids), model=model, label="欄位對映")

    for item in result.get("mappings", []):
        sid, key = item.get("id"), item.get("field_key")
        if sid in ids and (key in BY_KEY or key in ("__SKIP__", "__UNKNOWN__")):
            decisions[sid] = (key, int(item.get("ordinal", 0)),
                              float(item.get("confidence", 0.0)), "model",
                              str(item.get("label", ""))[:40])

    missing = [i for i in ids if i not in decisions]
    if missing:
        log.warning("模型漏答 %d 格，視為找不到對應 id=%s", len(missing), ",".join(missing[:8]))
        for sid in missing:
            decisions[sid] = ("__UNKNOWN__", 0, 0.0, "model", "")

    _renumber(slots, decisions)
    return decisions


def _renumber(slots: List[Slot], decisions: Dict[str, Decision]) -> None:
    """學歷／經歷的第幾筆，由模型指認的位置照列號排出來。

    模型有能力說出「這格是 education[].school」，但要它同時數對是第幾列並不可靠，
    而它一旦說了哪些格屬於同一欄，排序就是確定的事。
    """
    by_id = {s.id: s for s in slots}
    groups: Dict[Tuple, List[Tuple[int, str]]] = {}

    for sid, (key, *_rest) in decisions.items():
        slot = by_id.get(sid)
        if "[]" not in key or slot is None or "row" not in slot.loc:
            continue
        groups.setdefault((slot.loc["table"], slot.loc["col"], key), []).append(
            (slot.loc["row"], sid))

    for items in groups.values():
        for ordinal, (_row, sid) in enumerate(sorted(items)):
            key, _old, conf, source, label = decisions[sid]
            decisions[sid] = (key, ordinal, conf, source, label)


def _prompt(text: str, slots: List[Slot]) -> str:
    lines = ["可用的欄位代碼：", describe_fields(), "", "履歷表全文：", text, "", "待判斷的位置："]
    for s in slots:
        extra = f"（勾選題，選項：{'、'.join(s.options)}）" if s.options else ""
        current = f"（目前內容：{s.existing[:30]}）" if s.existing.strip() else ""
        lines.append(f"- {s.id}{extra}{current}")
    return "\n".join(lines)


def _schema(ids: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "minItems": len(ids),
                "maxItems": len(ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "field_key": {"type": "string", "enum": FIELD_KEYS},
                        "ordinal": {"type": "integer"},
                        "confidence": {"type": "number"},
                        "label": {"type": "string"},
                    },
                    "required": ["id", "field_key", "ordinal", "confidence", "label"],
                },
            }
        },
        "required": ["mappings"],
    }


# --------------------------------------------------------------------------
# 產生計畫
# --------------------------------------------------------------------------
def build_plan(slots: List[Slot], profile: Dict[str, Any],
               decisions: Dict[str, Decision], min_confidence: float = 0.60,
               allow_sensitive: bool = False) -> Tuple[List[FillOp], List[FillOp]]:
    from .schema import SENSITIVE_KEYS

    by_id = {s.id: s for s in slots}
    ops: List[FillOp] = []
    skipped: List[FillOp] = []

    for sid, (key, ordinal, conf, source, label) in decisions.items():
        slot = by_id.get(sid)
        if slot is None:
            continue

        reason = _reject(key, conf, min_confidence, allow_sensitive, SENSITIVE_KEYS)
        if reason:
            skipped.append(FillOp(slot, key, "", conf, source, label, reason, ordinal))
            continue

        value = get_value(profile, key, ordinal)
        if value in (None, ""):
            skipped.append(FillOp(slot, key, "", conf, source, label,
                                  "個人資料中此欄位為空", ordinal))
            continue

        if slot.kind == "checkbox":
            picked = _pick_option(slot.options, str(value))
            if not picked:
                skipped.append(FillOp(slot, key, str(value), conf, source, label,
                                      "勾選選項對不上", ordinal))
                continue
            value = picked

        ops.append(FillOp(slot, key, str(value), conf, source, label, ordinal=ordinal))

    ops.sort(key=lambda o: o.slot.id)
    skipped.sort(key=lambda o: o.slot.id)

    # 「個人資料為空」是正常情形（表格列數多於實際學經歷），其餘代表判斷有疑慮
    for s in skipped:
        emit = log.debug if s.note == "個人資料中此欄位為空" else log.warning
        emit("略過 %s → %s：%s", s.slot.id, s.field_key, s.note)
    return ops, skipped


def _reject(key: str, conf: float, min_confidence: float,
            allow_sensitive: bool, sensitive: set) -> str:
    if key in ("__SKIP__", "__UNKNOWN__"):
        return "模型判定非可填欄位或找不到對應"
    if key not in BY_KEY:
        return "此欄位已不存在，請重新指定"
    if key in sensitive and not allow_sensitive:
        return "敏感欄位，預設不自動填"
    if conf < min_confidence:
        return f"信心值 {conf:.2f} 低於門檻"
    return ""


def _pick_option(options: List[str], value: str) -> Optional[str]:
    """把 profile 的值對到表單上實際印出來的選項字串。"""
    target = _squash(value)
    for o in options:
        if _squash(o) == target:
            return o
    for o in options:
        squashed = _squash(o)
        if squashed and (squashed in target or target in squashed):
            return o
    return None


def _squash(text: str) -> str:
    return re.sub(r"[\s　:：*※()（）\[\]]+", "", text or "").lower()


# --------------------------------------------------------------------------
# profile 存取
# --------------------------------------------------------------------------
def get_value(profile: Dict[str, Any], key: str, ordinal: int = 0):
    if "[]" in key:
        head, tail = key.split("[].", 1)
        rows = profile.get(head) or []
        return _dig(rows[ordinal], tail) if ordinal < len(rows) else ""
    return _dig(profile, key)


def set_value(profile: Dict[str, Any], key: str, value: str, ordinal: int = 0) -> None:
    if "[]" in key:
        head, tail = key.split("[].", 1)
        rows = profile.setdefault(head, [])
        while len(rows) <= ordinal:
            rows.append({})
        _bury(rows[ordinal], tail, value)
    else:
        _bury(profile, key, value)


def _dig(obj: Any, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
        if cur is None:
            return ""
    return "、".join(str(x) for x in cur) if isinstance(cur, list) else cur


def _bury(obj: Dict[str, Any], path: str, value: str) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value

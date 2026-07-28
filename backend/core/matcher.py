"""
三層比對引擎 (Three-tier Matcher)
------------------------------------------------
把「空格」對到「欄位」，成本由低到高逐層升級：

  第 1 層  範本快取   同一份表格看過就記住 → 0 成本、100% 一致
  第 2 層  規則比對   別名表精確／包含比對 → 0 成本，實測可解 70~90% 標籤
  第 3 層  LLM 語意   只把剩下的殘餘丟給小模型 → 通常只剩 3~10 格

這就是為什麼「4B 的小模型就夠用」：LLM 每份履歷只需要處理個位數的難題，
而且是選擇題（受 enum 約束），不是自由生成。
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .schema import (ALIAS_INDEX, BY_KEY, SENSITIVE_KEYS, normalize_label)

log = logging.getLogger(__name__)

LLM_BATCH_SIZE = 12          # 一次丟給模型的空格數，太多會降低準確率
FUZZY_CUTOFF = 0.86          # 模糊比對門檻
SUBSTRING_MIN_COVERAGE = 0.5 # 包含比對時，短的一方至少要佔長的一方多少


@dataclass
class FillOp:
    anchor: Dict[str, Any]
    field_key: str
    value: str
    confidence: float
    source: str               # cache | rule | fuzzy | llm | manual
    note: str = ""
    ordinal: int = 0


# --------------------------------------------------------------------------
# 第 2 層：規則 / 模糊比對
# --------------------------------------------------------------------------
def rule_match(label: str) -> Tuple[Optional[str], float, str]:
    n = normalize_label(label)
    if not n:
        return None, 0.0, ""
    if n in ALIAS_INDEX:
        return ALIAS_INDEX[n], 0.99, "rule"

    # 包含比對：「姓名(必填)」→「姓名」；用最長的別名優先，避免「電話」蓋掉「緊急聯絡電話」。
    # 但兩邊長度必須相近：「地址」只佔「電子郵遞地址」的三分之一，那其實是 email。
    # 沒有這道門檻的話，錯誤對映會帶著 0.90 信心直接跳過模型那一層，把錯的值寫進履歷。
    hits = []
    for alias, key in ALIAS_INDEX.items():
        if not alias or not (alias in n or n in alias):
            continue
        coverage = min(len(alias), len(n)) / max(len(alias), len(n))
        if coverage >= SUBSTRING_MIN_COVERAGE:
            hits.append((alias, key))
    if hits:
        alias, key = max(hits, key=lambda x: len(x[0]))
        return key, 0.90, "rule"

    close = difflib.get_close_matches(n, list(ALIAS_INDEX.keys()), n=1, cutoff=FUZZY_CUTOFF)
    if close:
        return ALIAS_INDEX[close[0]], 0.75, "fuzzy"
    return None, 0.0, ""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def decide(anchors: List[Dict[str, Any]], backend,
           cached_map: Optional[Dict[str, str]] = None
           ) -> Dict[str, Tuple[str, float, str]]:
    """三層比對，決定每個 anchor 對到哪個欄位。回傳 anchor_id -> (key, conf, source)。

    與 build_plan 分開的理由：使用者事後修正某一格時，只需要改 decided 再重跑
    build_plan，不必重新解析檔案、更不必再呼叫模型。
    """
    cached_map = cached_map or {}
    decided: Dict[str, Tuple[str, float, str]] = {}
    pending: List[Dict[str, Any]] = []

    for a in anchors:
        # 第 1 層：範本快取
        if a["id"] in cached_map:
            decided[a["id"]] = (cached_map[a["id"]], 1.0, "cache")
            continue
        # sdt / formfield 的 tag 名稱本身就是標籤，直接走規則
        key, conf, src = rule_match(a["label"])
        if key:
            decided[a["id"]] = (key, conf, src)
        else:
            pending.append(a)

    log.info("快取 %d 格，規則層解出 %d 格，剩 %d 格交給模型",
             sum(1 for v in decided.values() if v[2] == "cache"),
             sum(1 for v in decided.values() if v[2] in ("rule", "fuzzy")),
             len(pending))

    # 第 3 層：LLM，分批處理
    for i in range(0, len(pending), LLM_BATCH_SIZE):
        batch = pending[i:i + LLM_BATCH_SIZE]
        try:
            results = backend.map_anchors(batch)
        except Exception as e:                       # 模型掛掉不該讓整個流程失敗
            results = [{"anchor_id": b["id"], "field_key": "__UNKNOWN__",
                        "confidence": 0.0} for b in batch]
            log.warning("模型呼叫失敗，該批 %d 格改為人工處理：%s", len(batch), e)
        valid_ids = {b["id"] for b in batch}
        for r in results:
            aid = r.get("anchor_id")
            key = r.get("field_key")
            # 白名單驗證：模型只能選我們給的欄位，其餘一律丟掉
            if aid in valid_ids and (key in BY_KEY or key in ("__SKIP__", "__UNKNOWN__")):
                decided[aid] = (key, float(r.get("confidence", 0.0)), "llm")
            else:
                log.warning("模型輸出被白名單擋下 anchor=%s key=%s", aid, key)
    return decided


def build_plan(anchors: List[Dict[str, Any]],
               profile: Dict[str, Any],
               decided: Dict[str, Tuple[str, float, str]],
               min_confidence: float = 0.60,
               allow_sensitive: bool = False) -> Tuple[List[FillOp], List[FillOp]]:
    """
    回傳 (ops, skipped)
      ops     : 準備寫入的操作（仍建議讓使用者過目）
      skipped : 沒填的空格與原因，方便使用者手動補
    """
    ops: List[FillOp] = []
    skipped: List[FillOp] = []
    ordinals = assign_ordinals(anchors, decided)

    by_id = {a["id"]: a for a in anchors}
    for aid, (key, conf, src) in decided.items():
        a = by_id.get(aid)
        if a is None:
            continue
        if key in ("__SKIP__", "__UNKNOWN__"):
            skipped.append(FillOp(a, key, "", conf, src, "模型判定非可填欄位或找不到對應"))
            continue
        if key in SENSITIVE_KEYS and not allow_sensitive:
            skipped.append(FillOp(a, key, "", conf, src, "敏感欄位，預設不自動填"))
            continue
        if conf < min_confidence:
            skipped.append(FillOp(a, key, "", conf, src, f"信心值 {conf:.2f} 低於門檻"))
            continue
        value = get_value(profile, key, ordinals.get(aid, 0))
        if value in (None, ""):
            skipped.append(FillOp(a, key, "", conf, src, "個人資料中此欄位為空"))
            continue
        if a["kind"] == "checkbox":
            picked = _pick_option(a.get("options", []), value)
            if not picked:
                skipped.append(FillOp(a, key, value, conf, src, "勾選選項對不上"))
                continue
            value = picked
        ops.append(FillOp(a, key, str(value), conf, src, ordinal=ordinals.get(aid, 0)))

    ops.sort(key=lambda o: o.anchor["id"])
    skipped.sort(key=lambda o: o.anchor["id"])

    # 「個人資料為空」是正常情形（表格列數多於實際學經歷），不值得 WARNING；
    # 其餘略過原因代表判斷有疑慮，要能在 log 直接看到。
    for s in skipped:
        emit = log.debug if s.note == "個人資料中此欄位為空" else log.warning
        emit("略過 %s「%s」→ %s：%s", s.anchor["id"], s.anchor["label"],
             s.field_key, s.note)
    return ops, skipped


def assign_ordinals(anchors, decided) -> Dict[str, int]:
    """
    處理『學歷／經歷』這種多列表格：同一個 (表格, 欄, 欄位代碼) 的空格
    依列號排序，第 1 列拿 education[0]、第 2 列拿 education[1]，依此類推。
    """
    groups: Dict[Tuple, List[Tuple[int, str]]] = {}
    by_id = {a["id"]: a for a in anchors}
    for aid, (key, _, _) in decided.items():
        if "[]" not in key:
            continue
        a = by_id.get(aid)
        if not a:
            continue
        loc = a.get("loc", {})
        gk = (loc.get("table"), loc.get("col"), key)
        groups.setdefault(gk, []).append((loc.get("row", 0), aid))

    out: Dict[str, int] = {}
    for _, items in groups.items():
        for i, (_, aid) in enumerate(sorted(items)):
            out[aid] = i
    return out


def get_value(profile: Dict[str, Any], key: str, ordinal: int = 0):
    """依欄位代碼從 profile 取值，支援 education[].school 這種列表路徑。"""
    if "[]" in key:
        head, tail = key.split("[].", 1)
        arr = profile.get(head) or []
        if ordinal >= len(arr):
            return ""
        item = arr[ordinal]
        return _dig(item, tail)
    return _dig(profile, key)


def set_value(profile: Dict[str, Any], key: str, value: str, ordinal: int = 0) -> None:
    """與 get_value 對稱的寫入，供匯入流程使用。列表不夠長時自動補齊。"""
    if "[]" in key:
        head, tail = key.split("[].", 1)
        arr = profile.setdefault(head, [])
        while len(arr) <= ordinal:
            arr.append({})
        _bury(arr[ordinal], tail, value)
    else:
        _bury(profile, key, value)


def _bury(obj: Dict[str, Any], path: str, value: str) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def _dig(obj: Any, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return ""
        if cur is None:
            return ""
    if isinstance(cur, list):
        return "、".join(str(x) for x in cur)
    return cur


def _pick_option(options: List[str], value: str) -> Optional[str]:
    """把 profile 的值對到表單上實際印出來的選項字串。"""
    if not options:
        return None
    v = normalize_label(str(value))
    for o in options:
        if normalize_label(o) == v:
            return o
    for o in options:
        no = normalize_label(o)
        if no and (no in v or v in no):
            return o
    return None

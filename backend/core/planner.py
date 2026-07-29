"""決定履歷表上每個位置該填什麼，並產生填寫計畫。

標籤驅動：表格上印的欄位標籤（姓名、行動電話…）是可靠的錨點，
「值填在標籤右邊或下面」由確定性的幾何規則決定；模型只出場一次，
處理對照表裡沒有的怪標籤。錨不住的位置留白待人工，不硬猜。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import document, llm
from .document import Slot
from .schema import (BLOCKED_LABELS, BY_KEY, BY_LABEL, DERIVED_FROM, FIELD_KEYS,
                     LABEL_ALIASES, describe_fields)

log = logging.getLogger(__name__)

LABEL_PROMPT = """這些是履歷表格上印的欄位標籤，請為每一個指出對應的欄位代碼。

規則：
1. 只能使用給定的欄位代碼。
2. 「｜」後面是這個標籤所在列的列首，用它判斷語意：
   「姓名｜緊急連絡人」是緊急連絡人的姓名（emergency.name），不是本人姓名。
3. 不是求職者要填的（公司內部欄位、簽章欄、說明文字）→ __SKIP__。
4. 是求職者要填、但清單裡沒有對應項目 → __UNKNOWN__。
5. 每個標籤都要輸出一次，label 照抄輸入的字串。"""


@dataclass
class FillOp:
    slot: Slot
    field_key: str
    value: str
    confidence: float
    source: str               # cache | model | manual
    label: str = ""           # 表格上印在這格旁邊的字，機械抽取自列首／欄首
    note: str = ""
    ordinal: int = 0


Decision = Tuple[str, int, float, str, str]   # field_key, ordinal, confidence, source, label

# 舊版由模型照抄標籤，常把 {{tbl1.r2.c6}} 位置標記一起抄回來；
# 快取裡可能還留著這種髒 label，讀出來時清掉
_MARKER_RE = re.compile(r"\{\{[^{}]*\}\}")


def _clean_label(raw: Any) -> str:
    return _MARKER_RE.sub("", str(raw or "")).strip()[:40]


# 標籤 → 欄位的確定性對照（squash 後精確比對），decide 與 align_labels 共用
LABEL_MAP = {re.sub(r"[\s　:：*※()（）\[\]]+", "", lbl).lower(): key
             for lbl, key in {**BY_LABEL, **LABEL_ALIASES}.items()}


def _mech_label(slot: Slot, headers: Dict[str, Dict[str, str]]) -> str:
    """這一格的標籤，機械抽取不經過模型（模型照抄既慢又會抄錯）。

    列首與欄首都在時，優先挑「對得上欄位定義」的那個——
    標籤在左的表單要列首、欄名在上的表格要欄首，對得上的就是對的方向。
    """
    h = headers.get(slot.id, {})
    row, col = h.get("row", ""), h.get("col", "")
    for cand in (row, col):
        if cand and _squash(cand) in LABEL_MAP:
            return cand[:40]
    return (row or col)[:40]


# --------------------------------------------------------------------------
# 決定對映
# --------------------------------------------------------------------------
def decide_by_anchor(path: str, slots: List[Slot], host: str, model: str,
                     cached: Optional[Dict[str, Any]] = None,
                     headers: Optional[Dict[str, Dict[str, str]]] = None
                     ) -> Dict[str, Decision]:
    """標籤驅動的對映：程式找標籤、定位置，模型只處理對照表外的標籤。

    反轉舊作法（枚舉所有空格、逐格問模型）：表格上印的標籤才是可靠的錨點，
    「值填在標籤右邊或下面」是確定性的幾何規則。錨不住的位置留白待人工，
    比模型硬猜填錯格安全；標籤全在對照表裡時，整條路零模型呼叫。
    """
    cached = cached or {}
    headers = headers or {}
    decisions: Dict[str, Decision] = {}
    pending: List[Slot] = []
    for slot in slots:
        hit = cached.get(slot.id)
        if hit:
            # 舊快取可能存到未清理的 label，讀出來時一併清
            decisions[slot.id] = (hit["field_key"], hit.get("ordinal", 0), 1.0,
                                  "cache", _clean_label(hit.get("label", "")))
        else:
            pending.append(slot)

    log.info("快取命中 %d 格，待錨定 %d 格", len(decisions), len(pending))
    if not pending:
        return decisions

    texts = document.table_texts(path)
    by_loc = {(s.loc["table"], s.loc["row"], s.loc["col"]): s
              for s in pending if "table" in s.loc}

    # ---- 蒐集錨點：標籤文字 → 它能餵到的可填位置 ----
    # (標籤, 位置們, 錨定方式, 同列列首) 列首是解析語意的上下文：
    # 「姓名」印在緊急連絡人那一列時是連絡人的姓名，不是本人的
    anchors: List[Tuple[str, List[Slot], str, str]] = []

    # 勾選格與段落位置的標籤印在自己身上（或空格前面），最可靠
    for s in pending:
        if s.kind == "checkbox":
            label = re.split(f"[{document.CHECKBOX_CHARS}{document.CHECKED_CHARS}]",
                             s.existing)[0].strip(" 　:：")
            if label:
                anchors.append((label, [s], "self", ""))
        elif "para" in s.loc:
            label = headers.get(s.id, {}).get("row", "")
            if label:
                anchors.append((label, [s], "self", ""))

    # 表格：印字又不是可填位置的格子當標籤，右邊找一格、下面找一排。
    # 合併儲存格在每個涵蓋座標重複出現：右掃從最右端做、下掃從最左端做，
    # 各一次就好——寬標題橫跨八欄時，每欄都往下錨定會把生日塞滿整排分位格
    for t, grid in enumerate(texts):
        for r, row in enumerate(grid):
            for c, text in enumerate(row):
                if not text.strip() or (t, r, c) in by_loc:
                    continue
                ctx = next((x for x in reversed(row[:c])
                            if x.strip() and x != text), "")
                if not (c + 1 < len(row) and row[c + 1] == text):
                    right = _scan_right(grid, by_loc, t, r, c)
                    if right:
                        anchors.append((text, right, "right", ctx))
                if not (c > 0 and row[c - 1] == text):
                    below = _scan_below(grid, by_loc, t, r, c)
                    if below:
                        anchors.append((text, below, "below", ctx))

    # ---- 標籤 → 欄位代碼：對照表優先，剩下的一次小呼叫問模型 ----
    # 對照表比對不看上下文（能精確對上欄位名的標籤本身就無歧義）；
    # 模型解析要看：同字不同列首（姓名｜緊急連絡人）是不同的欄位
    resolved: Dict[str, str] = {}
    unknown: Dict[str, str] = {}      # composite key → 給模型看的顯示字串
    blocked = [_squash(b) for b in BLOCKED_LABELS]
    for label, _targets, _mode, ctx in anchors:
        sq = _squash(label)
        if not sq or sq in LABEL_MAP:
            if sq and sq not in resolved:
                resolved[sq] = LABEL_MAP.get(sq, "")
            continue
        comp = f"{sq}|{_squash(ctx)}"
        if comp in unknown:
            continue
        if len(sq) <= 20 and not sq.isdigit() and not any(b in sq for b in blocked):
            shown = label.replace("\n", " ").strip()[:20]
            if ctx:
                shown += f"｜{ctx.replace(chr(10), ' ').strip()[:12]}"
            unknown[comp] = shown
    model_keys = _resolve_labels(unknown, host, model) if unknown else {}

    # ---- 指派：同格自帶 > 右鄰 > 下方；先到先得 ----
    for want in ("self", "right", "below"):
        for label, targets, mode, ctx in anchors:
            if mode != want:
                continue
            sq = _squash(label)
            key = resolved.get(sq) or model_keys.get(f"{sq}|{_squash(ctx)}", "")
            if key not in BY_KEY:
                continue
            source = "rule" if resolved.get(sq) else "model"
            if "[]" not in key and mode == "below":
                targets = targets[:1]      # 單值欄位只吃緊鄰的一格，不吃整欄
            for s in targets:
                if s.id not in decisions:
                    decisions[s.id] = (key, 0, 1.0 if source == "rule" else 0.85,
                                       source, label.replace("\n", " ")[:40])

    # ---- 錨不住的一律留白待人工；標籤用機械抽取的給使用者認格子 ----
    for s in pending:
        if s.id not in decisions:
            decisions[s.id] = ("__UNKNOWN__", 0, 0.0, "rule", _mech_label(s, headers))

    anchored = sum(1 for s in pending if decisions[s.id][0] in BY_KEY)
    log.info("錨定完成 可填=%d 留白=%d 對照表外標籤=%d",
             anchored, len(pending) - anchored, len(unknown))
    _renumber(slots, decisions)
    return decisions


def _scan_right(grid: List[List[str]], by_loc: Dict, t: int, r: int, c: int,
                limit: int = 8) -> List[Slot]:
    """標籤右邊第一個可填位置。撞到別的印字格就停——右邊沒有它的位置。"""
    row = grid[r]
    label = row[c]
    for cc in range(c + 1, min(len(row), c + 1 + limit)):
        s = by_loc.get((t, r, cc))
        if s is not None:
            return [s]
        if row[cc].strip() and row[cc] != label:
            return []
    return []


def _scan_below(grid: List[List[str]], by_loc: Dict, t: int, r: int, c: int,
                limit: int = 10) -> List[Slot]:
    """標籤（欄名）正下方連續的可填位置，撞到印字格為止——欄名式表格用。"""
    out: List[Slot] = []
    for rr in range(r + 1, min(len(grid), r + 1 + limit)):
        if c >= len(grid[rr]):
            break
        s = by_loc.get((t, rr, c))
        if s is not None:
            out.append(s)
        elif grid[rr][c].strip():
            break
    return out


def _resolve_labels(unknown: Dict[str, str], host: str, model: str) -> Dict[str, str]:
    """對照表沒有的標籤（含列首上下文），一次問模型。
    模型沒起來就全留白，不擋流程。回傳 {composite key: 欄位代碼}。"""
    shown = list(unknown.values())
    by_shown = {v: k for k, v in unknown.items()}
    schema = {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "minItems": len(shown), "maxItems": len(shown),
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": shown},
                        "field_key": {"type": "string", "enum": FIELD_KEYS},
                    },
                    "required": ["label", "field_key"],
                },
            }
        },
        "required": ["mappings"],
    }
    user = ("可用的欄位代碼：\n" + describe_fields() +
            "\n\n表格上的標籤：\n" + "\n".join(f"- {x}" for x in shown))
    try:
        result = llm.ask(host, LABEL_PROMPT, user, schema, model=model,
                         label=f"標籤對映:{len(shown)}個")
    except llm.LlmUnavailable:
        log.warning("模型不可用，對照表外的 %d 個標籤先留白", len(shown))
        return {}
    return {by_shown[m["label"]]: m.get("field_key", "")
            for m in result.get("mappings", []) if m.get("label") in by_shown}


ASSIGN_PROMPT = """履歷表格的每一列要填個人資料清單中的哪一筆？

規則：
1. 列首指明類別時，挑【內容相符】的那一筆，不是照順序數：
   「大學」列挑 degree 是學士的；「研究所」列挑碩士或博士的；
   「高中/專科」列挑高中、高職、專科的。
2. 清單裡沒有符合那一列的資料 → entry 填 -1（整列留白）。
3. 列首全是空白（純流水列）→ 依序 0、1、2…，清單用完就 -1。
4. 每一筆資料最多指派給一列。每一列都要輸出一次。

範例：清單 entry0 degree=學士、entry1 degree=碩士，
列 r1「高中/專科」r2「大　學」r3「研究所」r4「其　它」
→ r1=-1（沒有高中資料）、r2=0、r3=1、r4=-1"""


_PERIOD_KEYS = {f"{s}[].{f}" for s in ("education", "experience")
                for f in ("period", "start", "end")}


def align_labels(slots: List[Slot], decisions: Dict[str, Decision],
                 headers: Dict[str, Dict[str, str]]) -> Dict[str, Decision]:
    """確定性修正，不經過模型：

    1. 標籤／欄首與欄位定義的名稱完全一致 → 直接採用那個欄位。
       模型在密集表格常整組位移一格（應徵職務配到可到職日），這裡拉回來。
    2. 印著白名單外資訊（血型、身高、體重、年制…）的格子 → 一律不填。
    3. 期間欄照欄序：同一列兩格 → 左 start 右 end；只有一格 → period。
    只動模型判的格子；快取與手動修正不碰。
    """
    by_id = {s.id: s for s in slots}
    blocked = [_squash(b) for b in BLOCKED_LABELS]

    # 模型把同一列的兩格抄了同一個標籤時（月薪與任職期間都寫「任職期間」），
    # 用機械抽取的欄首仲裁：欄首對得上欄位定義的，以欄首為準
    dup: Dict[Tuple[int, int, str], List[str]] = {}
    for sid, (_k, _o, _c, src, label) in decisions.items():
        slot = by_id.get(sid)
        if src == "model" and label and slot is not None and "row" in slot.loc:
            dup.setdefault((slot.loc["table"], slot.loc["row"], _squash(label)),
                           []).append(sid)
    effective: Dict[str, str] = {}
    for sids in dup.values():
        if len(sids) < 2:
            continue
        for sid in sids:
            col_hdr = _squash(headers.get(sid, {}).get("col", ""))
            if col_hdr and col_hdr in LABEL_MAP:
                effective[sid] = col_hdr

    for sid, (key, ordinal, conf, src, label) in list(decisions.items()):
        if src != "model" or key == "__SKIP__":
            continue
        # 只看這一格自己的標籤。欄首／列首在密集表格常是隔壁欄位的字
        # （性別格的正上方印著身份證字號），拿來比對會誤殺
        text = effective.get(sid) or _squash(label)
        if not text:
            continue

        if key in BY_KEY and any(b in text for b in blocked):
            decisions[sid] = ("__SKIP__", 0, 1.0, src, label)
            log.info("標籤對齊 %s：%s → __SKIP__（白名單外欄位）", sid, key)
            continue

        target = LABEL_MAP.get(text)
        if target and target != key:
            decisions[sid] = (target, ordinal, 1.0, src, label)
            log.info("標籤對齊 %s：%s → %s", sid, key, target)

    # 期間欄配對：同列同類的期間位置，兩格拆起訖、單格用合成
    groups: Dict[Tuple[int, int, str], List[str]] = {}
    for sid, (key, *_r) in decisions.items():
        slot = by_id.get(sid)
        if key in _PERIOD_KEYS and slot is not None and "row" in slot.loc:
            groups.setdefault(
                (slot.loc["table"], slot.loc["row"], key.split("[].", 1)[0]),
                []).append(sid)
    for (_t, _row, section), sids in groups.items():
        sids.sort(key=lambda x: by_id[x].loc["col"])
        wanted = ([f"{section}[].period"] if len(sids) == 1 else
                  [f"{section}[].start"] +
                  ["__SKIP__"] * (len(sids) - 2) + [f"{section}[].end"])
        for sid, new_key in zip(sids, wanted):
            key, ordinal, conf, src, label = decisions[sid]
            # 錨定引擎的合併標題會讓同列兩格拿到同一個 period，這裡拆回起訖
            if src in ("model", "rule") and key != new_key:
                decisions[sid] = (new_key, ordinal, conf, src, label)
                log.info("期間對齊 %s：%s → %s", sid, key, new_key)
    return decisions


def assign_rows(slots: List[Slot], decisions: Dict[str, Decision],
                profile: Dict[str, Any], headers: Dict[str, Dict[str, str]],
                host: str, model: str) -> Dict[str, Decision]:
    """有語意列首的清單表格（高中/專科、大學、研究所…），由模型指認
    每一列對應清單的第幾筆，整列一起改 ordinal；沒有對應的整列 SKIP。

    _renumber 假設「表格第一列＝資料第一筆」，遇到分級列就整組錯位。
    這是個封閉選擇題，比逐格對映可靠得多。
    """
    by_id = {s.id: s for s in slots}

    # (table, section) -> {row: [sid...]}
    groups: Dict[Tuple[int, str], Dict[int, List[str]]] = {}
    for sid, (key, *_r) in decisions.items():
        slot = by_id.get(sid)
        if "[]" not in key or slot is None or "row" not in slot.loc:
            continue
        section = key.split("[].", 1)[0]
        groups.setdefault((slot.loc["table"], section), {}) \
              .setdefault(slot.loc["row"], []).append(sid)

    for (table, section), rows in groups.items():
        entries = profile.get(section) or []
        row_hdrs = {r: next((headers.get(sid, {}).get("row", "") for sid in sids
                             if headers.get(sid, {}).get("row")), "")
                    for r, sids in rows.items()}
        # 全部列都沒有列首（純流水列）→ 照順序即可，維持 _renumber 的結果
        if not any(row_hdrs.values()):
            continue

        assignment = _ask_rows(section, entries, sorted(row_hdrs.items()), host, model)
        if not assignment:
            continue
        # 保險：同一筆資料只能用一次，模型重複指派時保留最先出現的列
        seen: set = set()
        for r in sorted(assignment):
            e = assignment[r]
            if e >= 0:
                assignment[r] = -1 if e in seen else e
                seen.add(e)
        for r, sids in rows.items():
            entry = assignment.get(r)
            if entry is None:
                continue
            for sid in sids:
                key, _old, conf, src, label = decisions[sid]
                if entry < 0 or entry >= len(entries):
                    decisions[sid] = ("__SKIP__", 0, 1.0, src, label)
                else:
                    decisions[sid] = (key, entry, conf, src, label)
        log.info("列指派 %s tbl%d：%s", section, table,
                 {r: assignment.get(r) for r in sorted(rows)})
    return decisions


def _ask_rows(section: str, entries: List[Dict[str, Any]],
              rows: List[Tuple[int, str]], host: str, model: str) -> Dict[int, int]:
    lines = [f"個人資料 {section} 清單："]
    for i, row in enumerate(entries):
        desc = "　".join(f"{k}={v}" for k, v in row.items() if v not in (None, ""))
        lines.append(f"- entry {i}：{desc}")
    if not entries:
        lines.append("（清單是空的，所有列都填 -1）")
    lines.append("")
    lines.append("表格的列：")
    for r, hdr in rows:
        lines.append(f"- row {r}：列首「{hdr or '（空白）'}」")

    schema = {
        "type": "object",
        "properties": {
            "assignments": {
                "type": "array",
                "minItems": len(rows), "maxItems": len(rows),
                "items": {
                    "type": "object",
                    "properties": {
                        "row": {"type": "integer", "enum": [r for r, _ in rows]},
                        "entry": {"type": "integer",
                                  "enum": list(range(-1, len(entries)))},
                    },
                    "required": ["row", "entry"],
                },
            }
        },
        "required": ["assignments"],
    }
    try:
        result = llm.ask(host, ASSIGN_PROMPT, "\n".join(lines), schema,
                         model=model, label=f"列指派:{section}")
    except llm.LlmUnavailable:
        return {}
    valid_rows = {r for r, _ in rows}
    return {int(a["row"]): int(a["entry"])
            for a in result.get("assignments", [])
            if int(a.get("row", -9)) in valid_rows}


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
    # 包含比對只在雙方都夠長時才有意義。「可到職日 □隨時 □__週 ■8月17日」
    # 會解析出選項「8」，單字元一比就命中 2026-08-17，把日期勾成「8」。
    for o in options:
        squashed = _squash(o)
        if len(squashed) < 2 or len(target) < 2:
            continue
        if squashed in target or target in squashed:
            return o
    return None


def _squash(text: str) -> str:
    return re.sub(r"[\s　:：*※()（）\[\]]+", "", text or "").lower()


# --------------------------------------------------------------------------
# profile 存取
# --------------------------------------------------------------------------
def get_value(profile: Dict[str, Any], key: str, ordinal: int = 0):
    stored = _stored(profile, key, ordinal)
    if stored:
        return stored

    # 表格只印一欄「就學期間」時，用入學與畢業合成
    parts = DERIVED_FROM.get(key)
    if parts:
        start, end = (_stored(profile, p, ordinal) for p in parts)
        if start and end:
            return f"{start}－{end}"
        return start or end
    return stored


def _stored(profile: Dict[str, Any], key: str, ordinal: int):
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

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
from .schema import (BLOCKED_LABELS, BY_KEY, BY_LABEL, DERIVED_FROM, FIELD_KEYS,
                     LABEL_ALIASES, describe_fields)

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


VERIFY_PROMPT = """你是履歷表單填寫的審核員。第一輪對映已為每個位置配好欄位，
現在逐格檢查「這個值放進這一格」對不對。

每個位置都附上它所在列的列首與欄首——這兩個是直接從表格抽出來的，可靠
（窄欄密集處欄首可能偏一格，可與標籤互相印證）。

規則：
1. 這一格的意義由列首＋欄首＋標籤決定。配錯欄位就【改成正確的欄位】，
   例如標籤「希望待遇」卻配了 applied_position，要改成 expected_salary。
   只有個人資料裡真的沒有對應資訊時才選 __SKIP__，不要輕易放棄。
2. 個人資料裡不存在的項目（身分證字號、血型、身高、日/夜間部、幾年制、
   部門名稱等），一律 __SKIP__。
3. 期間欄的判斷：同一列有【兩個】期間位置時，左邊 start、右邊 end；
   只有【一個】位置時用 period（起訖合成）。
4. ordinal 照抄輸入值，不要改。
5. 判斷正確的就照抄。每個 id 都要輸出一次。"""

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


def verify(slots: List[Slot], decisions: Dict[str, Decision],
           profile: Dict[str, Any], headers: Dict[str, Dict[str, str]],
           host: str, model: str) -> Dict[str, Decision]:
    """第二輪：模型逐格審核即將填入的值。回傳修正後的 decisions。

    第一輪看的是攤平全文，窄欄與清單列常錯位；這一輪每格附上機械抽取的
    列首／欄首與個人資料摘要，資訊密度高得多。改判的格子直接覆寫，
    ordinal 採用模型指定的（不再 _renumber——挑第幾筆正是這一輪的工作）。
    """
    fills = []
    for sid, (key, ordinal, conf, src, label) in decisions.items():
        if key in ("__SKIP__", "__UNKNOWN__") or key not in BY_KEY:
            continue
        value = get_value(profile, key, ordinal)
        if value in (None, ""):
            continue
        fills.append({"id": sid, "key": key, "ordinal": ordinal,
                      "value": str(value), "label": label})
    if not fills:
        return decisions

    ids = [f["id"] for f in fills]
    result = llm.ask(host, VERIFY_PROMPT,
                     _verify_prompt(fills, profile, headers),
                     _schema(ids), model=model, label="填寫審核")

    changed = 0
    for item in result.get("mappings", []):
        sid, key = item.get("id"), item.get("field_key")
        if sid not in ids or (key not in BY_KEY and key not in ("__SKIP__", "__UNKNOWN__")):
            continue
        old_key, old_ord, old_conf, src, label = decisions[sid]
        # ordinal 不採用模型的：第幾筆由 assign_rows／_renumber 決定
        if key != old_key:
            decisions[sid] = (key, old_ord, float(item.get("confidence", 0.0)),
                              "model", label)
            changed += 1
            log.info("審核改判 %s：%s → %s", sid, old_key, key)
    log.info("審核完成 檢查=%d 改判=%d", len(fills), changed)
    return decisions


_PERIOD_KEYS = {f"{s}[].{f}" for s in ("education", "experience")
                for f in ("period", "start", "end")}


def align_labels(slots: List[Slot], decisions: Dict[str, Decision],
                 headers: Dict[str, Dict[str, str]]) -> Dict[str, Decision]:
    """確定性修正，不經過模型：

    1. 標籤／欄首與欄位定義的名稱完全一致 → 直接採用那個欄位。
       模型在密集表格常整組位移一格（應徵職務配到可到職日），這裡拉回來。
    2. 印著白名單外資訊（身分證、血型、日/夜、年制…）的格子 → 一律不填。
    3. 期間欄照欄序：同一列兩格 → 左 start 右 end；只有一格 → period。
    只動模型判的格子；快取與手動修正不碰。
    """
    by_id = {s.id: s for s in slots}
    label_map = {_squash(lbl): key
                 for lbl, key in {**BY_LABEL, **LABEL_ALIASES}.items()}
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
            if col_hdr and col_hdr in label_map:
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

        target = label_map.get(text)
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
            if src == "model" and key != new_key:
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


def _verify_prompt(fills: List[Dict[str, Any]], profile: Dict[str, Any],
                   headers: Dict[str, Dict[str, str]]) -> str:
    lines = ["可用的欄位代碼：", describe_fields(), "", "個人資料："]
    for section in ("basic", "contact", "job", "skills", "emergency"):
        data = profile.get(section)
        if isinstance(data, dict):
            for k, v in data.items():
                if v not in (None, ""):
                    lines.append(f"- {section}.{k} = {v}")
    for section in ("education", "experience"):
        for i, row in enumerate(profile.get(section) or []):
            desc = "　".join(f"{k}={v}" for k, v in row.items() if v not in (None, ""))
            lines.append(f"- {section}[{i}]：{desc}")

    lines.append("")
    lines.append("待審核的位置（列首／欄首＝這一格在表格上的位置）：")
    for f in fills:
        h = headers.get(f["id"], {})
        where = []
        if h.get("row"):
            where.append(f"列首「{h['row']}」")
        if h.get("col"):
            where.append(f"欄首「{h['col']}」")
        # 窄欄密集區的欄首可能偏一格，標籤是第一輪從全文抄的，兩者互補
        if f["label"]:
            where.append(f"標籤「{f['label']}」")
        lines.append(f"- {f['id']} {' '.join(where)}："
                     f"第一輪判 {f['key']} 第{f['ordinal']}筆，將填入「{f['value'][:40]}」")
    return "\n".join(lines)


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

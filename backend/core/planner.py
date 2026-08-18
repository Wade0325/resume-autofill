"""決定履歷表上每個位置該填什麼，並產生填寫計畫。

標籤驅動：表格上印的欄位標籤（姓名、行動電話…）是可靠的錨點，
「值填在標籤右邊或下面」由確定性的幾何規則決定；模型只出場一次，
處理對照表裡沒有的怪標籤。錨不住的位置留白待人工，不硬猜。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from . import document, llm
from .document import Slot
from .schema import (BLOCKED_LABELS, BY_KEY, BY_LABEL, DERIVED_FROM, FIELD_KEYS,
                     LABEL_ALIASES, OPTION_SYNONYMS, describe_fields)

log = logging.getLogger(__name__)

LABEL_PROMPT = """這些是履歷表格上印的字，請為每一則判斷兩件事：對應哪個欄位、這幾個字是什麼。

field_key：
1. 只能使用給定的欄位代碼。
2. 「｜」後面是這一則旁邊印的字（左邊／上面），用它判斷語意：
   「姓名｜列首:緊急聯絡人」是聯絡人的姓名（emergency.name），不是本人姓名。
3. 不是求職者要填的（公司內部欄位、簽章欄、說明文字）→ __SKIP__。
4. 清單裡沒有對應項目 → __UNKNOWN__。

role：這幾個字本身是什麼？只看字，不用管排版留了多少空白。
- 名稱＝這幾個字是欄位的名字，人在旁邊的空格寫答案
    「姓　　名」→ 名稱（「姓名」就是欄位的名字，中間的空白只是把字撐開）
    「就 學 期 間」「服務單位」「關　係」「稱謂」→ 名稱
- 格式＝這幾個字是值的單位或格式，數字就寫在這些字前面的空白裡
    「自　　年　　月」→ 格式（年、月是日期的單位）
    「　年　月　日」→ 格式
    「公分/　　公斤」→ 格式（公分、公斤是單位）
    「血型：　　型」→ 格式（型是單位）

每一則都要輸出一次，label 照抄輸入的字串。"""


# 這份表格真的分了區才附上去：沒有區塊的表格看到這段，會把「姓名」
# 一律當成本人的，反而丟掉家人與諮詢人的欄位（實測少 4 個欄位）
ZONE_RULE = """

補充：有些則標了「區塊:XXX」，那是它在表格裡所屬的區段，最能分辨同名的欄位——
「姓名｜區塊:家庭成員」是家人的姓名（family[].name），不是本人姓名。"""


@dataclass
class FillOp:
    slot: Slot
    field_key: str
    value: str
    source: str               # cache | model | manual
    label: str = ""           # 表格上印在這格旁邊的字，機械抽取自列首／欄首
    note: str = ""
    ordinal: int = 0
    clear: Tuple[str, ...] = ()   # 勾這個之前要先還原的同組選項


class Decision(NamedTuple):
    """一個位置的對映決策。存進 DB 時仍以四元 list 序列化，格式不變。"""
    field_key: str
    ordinal: int
    source: str       # rule | learned | model | cache | manual
    label: str

# 舊版由模型照抄標籤，常把 {{tbl1.r2.c6}} 位置標記一起抄回來；
# 快取裡可能還留著這種髒 label，讀出來時清掉
def _clean_label(raw: Any) -> str:
    return document.MARKER_RE.sub("", str(raw or "")).strip()[:40]


def _squash(text: str) -> str:
    return re.sub(r"[\s　:：*※()（）\[\]]+", "", text or "").lower()


# 標籤 → 欄位的確定性對照（squash 後精確比對），decide 與 align_labels 共用
LABEL_MAP = {_squash(lbl): key for lbl, key in {**BY_LABEL, **LABEL_ALIASES}.items()}


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


def decide_by_anchor(texts: List[List[List[str]]], slots: List[Slot],
                     host: str, model: str,
                     cached: Optional[Dict[str, Any]] = None,
                     headers: Optional[Dict[str, Dict[str, str]]] = None,
                     learned: Optional[Dict[str, Any]] = None,
                     allowed: Optional[List[str]] = None
                     ) -> Dict[str, Decision]:
    """標籤驅動的對映：程式找標籤、定位置，模型只處理對照表外的標籤。
    texts 是 ParsedDoc.table_texts() 的表格文字網格。

    反轉舊作法（枚舉所有空格、逐格問模型）：表格上印的標籤才是可靠的錨點，
    「值填在標籤右邊或下面」是確定性的幾何規則。錨不住的位置留白待人工，
    比模型硬猜填錯格安全；標籤全在對照表裡時，整條路零模型呼叫。

    allowed 是模型通篇讀過空白表格後列出的「這份表格要填哪些欄位」
    （見 reader.list_fields）。逐格判讀時那些欄位標★，模型優先從裡面挑，
    但沒標★的仍然選得到——實測拿它當硬性約束（把其他欄位從文法裡拿掉）
    會擋掉真的要填的欄位：清單漏一個，那個欄位就再也填不進去。

    learned 是使用者修正累積出來的「標籤→欄位」全域字典（跨表格通用，
    見 service.apply_fixes 的學習端）：A 公司教過的「服務單位＝公司名稱」，
    B 公司的表格直接受益。解析順位：內建對照表 → 學過的 → 問模型。
    """
    cached = cached or {}
    headers = headers or {}
    learned_keys = {sq: v.get("field_key", "") for sq, v in (learned or {}).items()}
    decisions: Dict[str, Decision] = {}
    pending: List[Slot] = []
    for slot in slots:
        hit = cached.get(slot.id)
        if hit:
            # 舊快取可能存到未清理的 label，讀出來時一併清
            decisions[slot.id] = Decision(hit["field_key"], hit.get("ordinal", 0),
                                          "cache", _clean_label(hit.get("label", "")))
        else:
            pending.append(slot)

    log.info("快取命中 %d 格，待錨定 %d 格", len(decisions), len(pending))
    if not pending:
        return decisions

    # 底線位置不進來搶：同一格的勾選群才是旁邊標籤要錨定的對象，
    # 底線自己會用「前面印的字」當標籤。
    # 一格可能有好幾個位置（就學期間的「自　年　月」「至　年　月」各一個），
    # 只留一個的話另一個永遠錨不住，所以一個座標存一串
    by_loc: Dict[Tuple[int, int, int], List[Slot]] = {}
    for s in pending:
        if "table" in s.loc and "blank_index" not in s.loc:
            by_loc.setdefault((s.loc["table"], s.loc["row"], s.loc["col"]), []).append(s)
    # 合併儲存格在涵蓋的每個座標都印著同一段字，位置卻只掛在最左上那格。
    # 不把整片都算成同一格的話，右緣會被當成另一個標籤問一次，
    # 同一格就會拿到兩個互相矛盾的答案
    covered: Dict[Tuple[int, int, int], List[Slot]] = dict(by_loc)
    for (t, r, c), ss in by_loc.items():
        text = texts[t][r][c]
        if not text.strip():
            continue
        for rr in range(r, len(texts[t])):
            if c >= len(texts[t][rr]) or texts[t][rr][c] != text:
                break
            for cc in range(c, len(texts[t][rr])):
                if texts[t][rr][cc] != text:
                    break
                covered[(t, rr, cc)] = ss
    # (標籤, 位置們, 錨定方式, 旁邊印的字) 上下文是解析語意的依據：
    # 「姓名」印在緊急連絡人那一列時是連絡人的姓名，不是本人的
    anchors: List[Tuple[str, List[Slot], str, str]] = []

    # 勾選格與段落位置的標籤印在自己身上（或空格前面），最可靠
    for s in pending:
        if s.kind == "checkbox":
            label = re.split(f"[{document.CHECKBOX_CHARS}{document.CHECKED_CHARS}]",
                             s.existing)[0].strip(" 　:：")
            if label:
                anchors.append((label, [s], "self", ""))
        elif s.kind == "print" and "table" not in s.loc:
            anchors.append((s.existing, [s], "self", ""))
        elif "para" in s.loc or "blank_index" in s.loc:
            label = headers.get(s.id, {}).get("row", "")
            if label:
                anchors.append((label, [s], "self", ""))

    # 表格：印著字的格子當標籤，右邊找一格、下面找一排。
    # 合併儲存格在每個涵蓋座標重複出現：右掃從最右端做、下掃從最左端做，
    # 各一次就好——寬標題橫跨八欄時，每欄都往下錨定會把生日塞滿整排分位格
    has_next: set = set()          # 這段字的右邊或下面真的有空格可填
    can_self: set = set()          # 這段字自己插得下值（有 print 位置）
    filled = {(s.loc["table"], s.loc["row"], s.loc["col"])
              for s in pending if s.kind == "cell" and s.existing.strip()}
    for t, grid in enumerate(texts):
        zones = regions(grid, {(r, c) for (tt, r, c) in filled if tt == t})
        for r, row in enumerate(grid):
            for c, text in enumerate(row):
                # 使用者填過的值不是標籤（那格是待覆蓋的位置，kind=cell）
                if not text.strip() or any(s.kind == "cell"
                                           for s in covered.get((t, r, c), ())):
                    continue
                ctx = _ctx(grid, r, c, zones)
                # 印著字又插得下值的格子：那段字既可能是「欄位名稱」（值填旁邊
                # 那格），也可能是「印好的提示」（人就寫在字中間的空白）。
                # 兩種都當候選，由模型的 where 決定——規則分不出來的是語意。
                # 兩邊共用同一個問題（同樣的字＋同樣的上下文），才不會出現
                # 「當標籤時算 A 欄位、當提示時算 B 欄位」這種自相矛盾的答案
                mine = [s for s in covered.get((t, r, c), ()) if s.kind == "print"]
                for s in mine:
                    anchors.append((s.existing, [s], "self", ctx))
                    can_self.add(_squash(s.existing))
                label = mine[0].existing if mine else text
                if not (c + 1 < len(row) and row[c + 1] == text):
                    right = _scan_right(grid, by_loc, t, r, c)
                    if right:
                        anchors.append((label, right, "right", ctx))
                        has_next.add(_squash(label))
                if not (c > 0 and row[c - 1] == text):
                    below = _scan_below(grid, by_loc, t, r, c)
                    if below:
                        anchors.append((label, below, "below", ctx))
                        # 下方只認空白格子：欄名底下是一整欄空格才算「值填在下面」，
                        # 底下是別區的勾選題（「希望待遇：」下面就是負債狀況）不算，
                        # 那種要讓值寫回標籤自己留的空白
                        if any(s.kind == "cell" for s in below):
                            has_next.add(_squash(label))

    # 對照表與學過的比對不看上下文（能精確對上的標籤本身就無歧義，
    # 泛用短標籤在學習端就被擋掉了）；模型解析要看：
    # 同字不同列首（姓名｜緊急連絡人）是不同的欄位
    resolved: Dict[str, str] = {}
    known: Dict[str, str] = {}        # 學過的命中（值可能是 __SKIP__＝學過「這不用填」）
    unknown: Dict[str, str] = {}      # composite key → 給模型看的顯示字串
    settled: Dict[str, str] = {}      # 這份表格裡已經確認的欄位名稱，當模型的範例
    blocked = [_squash(b) for b in BLOCKED_LABELS]
    for label, _targets, mode, ctx in anchors:
        sq = _squash(label)
        if not sq or sq in LABEL_MAP:
            if sq and sq not in resolved:
                resolved[sq] = LABEL_MAP.get(sq, "")
                if resolved[sq]:
                    settled[label.replace("\n", " ").strip()] = resolved[sq]
            continue
        if sq in learned_keys:
            known[sq] = learned_keys[sq]
            continue
        comp = f"{sq}|{_squash(ctx)}"
        if comp in unknown:
            continue
        # 印在自己身上的標籤（勾選題整句問句）可以長一點；旁邊格子當標籤的
        # 一長就多半是說明文字，超過就別送進模型了
        cap = 32 if mode == "self" else 20
        if len(sq) <= cap and not sq.isdigit() and not any(b in sq for b in blocked):
            shown = label.replace("\n", " ").strip()[:cap]
            if ctx:
                shown += f"｜{ctx}"
            unknown[comp] = shown
    answers = _resolve_labels(unknown, settled, allowed, host, model) if unknown else {}

    # self/right/below＝同格自帶 > 右鄰 > 下方，先到先得
    for want in ("self", "right", "below"):
        for label, targets, mode, ctx in anchors:
            if mode != want:
                continue
            sq = _squash(label)
            hit = answers.get(f"{sq}|{_squash(ctx)}")
            key = resolved.get(sq) or known.get(sq) or (hit[0] if hit else "")
            # __SKIP__／__UNKNOWN__ 也是有效結論：使用者教過「這不用填」或
            # 模型明確判過的，要保留下來，不能掉進兜底變成「找不到對應」
            if key not in BY_KEY and key not in ("__SKIP__", "__UNKNOWN__"):
                continue
            source = ("rule" if resolved.get(sq)
                      else "learned" if known.get(sq) else "model")
            # 值寫在哪：模型說了算；對照表與學過的標籤是欄位名稱，值填旁邊，
            # 但旁邊要真的有空格——「希望待遇：」右邊下面都沒空位時，
            # 值就寫在它自己留的空白上
            # 值寫在哪：以模型判的「名稱／格式」為主，但兩個結構事實蓋過它——
            # 文件段落沒有「旁邊那格」（「中文姓名：___ 英文名：」是一整段），
            # 值只能寫在自己身上；反過來，這段字自己插不下值
            # （「服役資歷」四個字沒留空白）就只能是名稱，值填旁邊
            where = hit[1] if hit else ("next" if sq in has_next else "self")
            if sq not in has_next and "table" not in targets[0].loc:
                where = "self"
            if where == "self" and sq not in can_self:
                where = "next"
            if mode == "self" and targets[0].kind == "print" and where != "self":
                continue
            if mode in ("right", "below") and where == "self":
                continue
            # 「這格不用填」是對這一格說的，不是對旁邊那格：「學　歷」判成
            # __SKIP__ 時不該連右邊的就讀學校一起封掉
            if key not in BY_KEY and mode != "self":
                continue
            if "[]" not in key and mode == "below":
                # 單值欄位只吃緊鄰的一格，不吃整欄（那格自己可能有好幾個位置）
                head = (targets[0].loc["row"], targets[0].loc["col"])
                targets = [s for s in targets
                           if (s.loc["row"], s.loc["col"]) == head]
            for s in targets:
                if s.id not in decisions:
                    decisions[s.id] = Decision(key, 0, source,
                                               label.replace("\n", " ")[:40])

    # 錨不住的一律留白待人工，標籤用機械抽取的給使用者認格子
    for s in pending:
        if s.id not in decisions:
            decisions[s.id] = Decision("__UNKNOWN__", 0, "rule",
                                       _mech_label(s, headers))

    anchored = sum(1 for s in pending if decisions[s.id].field_key in BY_KEY)
    log.info("錨定完成 可填=%d 留白=%d 學過的標籤=%d 對照表外=%d",
             anchored, len(pending) - anchored, len(known), len(unknown))
    _renumber(slots, decisions)
    return decisions


def regions(grid: List[List[str]],
            taken: Optional[set] = None) -> Dict[int, str]:
    """列號 -> 這一列屬於哪個區塊。機械抽取，不經過模型。

    履歷表的區塊標題有兩種印法，都在這裡認：直排合併在最左邊幾欄的
    （「學　歷」一格佔三列），以及橫跨整列的短標題。整段說明文字不算——
    宣告事項那種長問句橫跨整列，當區塊名只會洗掉真正的上下文。

    taken 是使用者填過值的座標：那些格子印的是這個人的資料（「中文：郭韋德」
    也可能垂直合併），不是區塊標題。
    """
    taken = taken or set()
    out: Dict[int, str] = {}

    def is_title(text: str) -> bool:
        """區塊標題長什麼樣：短、而且只有一個名字。
        「中文：郭韋德」（標籤配值）、「健康狀況：□優 □良」（勾選題）
        都是欄位不是區塊——它們一旦被當成區塊名，整區的上下文就被洗掉了。
        """
        return (0 < len(_squash(text)) <= 12
                and not re.search(r"[:：]\s*\S", text)
                and not any(ch in text for ch in
                            document.CHECKBOX_CHARS + document.CHECKED_CHARS))
    for r, row in enumerate(grid):
        if (r, 0) in taken:
            continue
        if row and len(set(row)) == 1 and is_title(row[0]):
            for rr in range(r + 1, len(grid)):
                if grid[rr] and len(set(grid[rr])) == 1:
                    break
                out[rr] = row[0]
    for r, row in enumerate(grid):
        for c, cell in enumerate(row[:3]):
            if not cell.strip() or (r, c) in taken:
                continue
            last = r
            while (last + 1 < len(grid) and c < len(grid[last + 1])
                   and grid[last + 1][c] == cell):
                last += 1
            if last > r and is_title(cell):   # 直排合併＋長得像標題才算
                for rr in range(r, last + 1):
                    out[rr] = cell
    return out


def _ctx(grid: List[List[str]], r: int, c: int,
         zones: Optional[Dict[int, str]] = None) -> str:
    """這一格旁邊印的字：所屬區塊、同列往左、同欄往上第一格有字的。
    模型判語意全靠它——「姓名｜區塊:家庭成員」是家人的姓名，
    「自　年　月｜欄首:就學期間」是入學年月。

    區塊要單獨給：「家 庭 成 員」是垂直合併的大格，「姓名」那一格的列首
    是隔壁的「稱謂」，光看列首欄首永遠看不到自己在哪一區。
    """
    # 合併儲存格在左邊／上面重複出現的是自己，不是旁邊那格
    text = grid[r][c]
    row_hdr = next((x for x in reversed(grid[r][:c]) if x.strip() and x != text), "")
    col_hdr = next((grid[rr][c] for rr in range(r - 1, -1, -1)
                    if c < len(grid[rr]) and grid[rr][c].strip()
                    and grid[rr][c] != text), "")
    zone = (zones or {}).get(r, "")
    if zone == text or _squash(zone) in (_squash(row_hdr), _squash(col_hdr)):
        zone = ""                             # 已經是列首欄首了，不必重複
    parts = [f"{name}:{x.replace(chr(10), ' ').strip()[:12]}"
             for name, x in (("區塊", zone), ("列首", row_hdr), ("欄首", col_hdr))
             if x.strip()]
    return "｜".join(parts)


def _scan_right(grid: List[List[str]], by_loc: Dict, t: int, r: int, c: int,
                limit: int = 8) -> List[Slot]:
    """標籤右邊第一格的可填位置。撞到別的印字格就停——右邊沒有它的位置。"""
    row = grid[r]
    label = row[c]
    for cc in range(c + 1, min(len(row), c + 1 + limit)):
        found = _empty_at(by_loc, t, r, cc)
        if found:
            return found
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
        found = _empty_at(by_loc, t, rr, c)
        if found:
            out.extend(found)
        elif grid[rr][c].strip():
            break
    return out


def _empty_at(by_loc: Dict, t: int, r: int, c: int) -> List[Slot]:
    """這一格有沒有「空著等人寫」的位置。

    印著字的位置（kind=print）不算：那格要不要填、填什麼，由它自己那則判讀
    決定。算進來的話，直排的標籤欄（姓名／婚姻／出生地各一列）會一路往下
    錨定——「姓名」把值寫進「婚姻」那格，整欄跟著錯開一格。
    """
    return [s for s in by_loc.get((t, r, c), ()) if s.kind != "print"]


def _resolve_labels(unknown: Dict[str, str], settled: Dict[str, str],
                    allowed: Optional[List[str]],
                    host: str, model: str) -> Dict[str, Tuple[str, str]]:
    """對照表沒有的字（含旁邊印的字當上下文），一次問模型：
    這是哪個欄位、值寫在旁邊（next）還是就寫在這幾個字中間（self）。
    回傳 {composite key: (欄位代碼, where)}。

    模型出問題就往上拋——這一步是填寫的主幹，靜靜留白只會讓使用者拿到
    一份半空的履歷卻不知道為什麼。
    """
    by_shown = {v: k for k, v in unknown.items()}
    out: Dict[str, Tuple[str, str]] = {}
    todo = list(unknown.values())
    # 分批問。一次丟幾十則進去，模型會整批放棄（實測 81 則只認得出 3 個欄位，
    # 分成每批 16 則是 38 個）。總題數一樣，所以不會比較慢
    for i in range(0, len(todo), BATCH):
        out.update(_ask_cells(todo[i:i + BATCH], by_shown, settled, allowed, host, model))
    return out


def _ask_cells(shown: List[str], by_shown: Dict[str, str], settled: Dict[str, str],
               allowed: Optional[List[str]], host: str,
               model: str) -> Dict[str, Tuple[str, str]]:
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
                        "role": {"type": "string", "enum": ["名稱", "格式"]},
                    },
                    "required": ["label", "field_key", "role"],
                },
            }
        },
        "required": ["mappings"],
    }
    # 對照表認得的欄位名稱不必問模型，但要放進提示當範例：只把「認不出來的」
    # 送過去，模型看到的是一份偏斜的樣本，會把「姓　　名」也當成填字的空格
    examples = ""
    if settled:
        examples = ("\n\n這份表格裡已經確認的欄位名稱（role 都是「名稱」）：\n"
                    + "\n".join(f"- {k} → {v}" for k, v in list(settled.items())[:12]))
    star = ("（★＝通篇讀過後判斷這份表格有問的欄位，優先從它們裡面挑；"
            "沒有★的也還是選得到）\n" if allowed else "")
    user = ("可用的欄位代碼：\n" + star + describe_fields(mark=allowed) + examples +
            "\n\n要判斷的字：\n" + "\n".join(f"- {x}" for x in shown))
    system = LABEL_PROMPT + (ZONE_RULE if any("區塊:" in x for x in shown) else "")
    result = llm.ask(host, system, user, schema, model=model,
                     label=f"格子判讀:{len(shown)}則")
    return {by_shown[m["label"]]:
            (m.get("field_key", ""), "self" if m.get("role") == "格式" else "next")
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

# 一次問模型幾則格子判讀。見 _resolve_labels：問多了整批答不出來
BATCH = 16


def align_labels(slots: List[Slot], decisions: Dict[str, Decision],
                 headers: Dict[str, Dict[str, str]]) -> Dict[str, Decision]:
    """確定性修正，不經過模型：

    1. 標籤／欄首與欄位定義的名稱完全一致 → 直接採用那個欄位。
       模型在密集表格常整組位移一格（希望待遇配到可到職日），這裡拉回來。
    2. 印著白名單外資訊（血型、身高、體重、年制…）的格子 → 一律不填。
    3. 期間欄照版面順序：同一列兩個位置 → 前 start 後 end；只有一個 → period。
       （同一格分兩行印「自　年　月」「至　年　月」也算兩個位置）
    只動模型判的格子；快取與手動修正不碰。
    """
    by_id = {s.id: s for s in slots}
    blocked = [_squash(b) for b in BLOCKED_LABELS]

    # 模型把同一列的兩格抄了同一個標籤時（月薪與任職期間都寫「任職期間」），
    # 用機械抽取的欄首仲裁：欄首對得上欄位定義的，以欄首為準
    dup: Dict[Tuple[int, int, str], List[str]] = {}
    for sid, (_k, _o, src, label) in decisions.items():
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

    for sid, (key, ordinal, src, label) in list(decisions.items()):
        if src != "model" or key == "__SKIP__":
            continue
        # 只看這一格自己的標籤。欄首／列首在密集表格常是隔壁欄位的字
        # （性別格的正上方印著身份證字號），拿來比對會誤殺
        text = effective.get(sid) or _squash(label)
        if not text:
            continue

        if key in BY_KEY and any(b in text for b in blocked):
            decisions[sid] = Decision("__SKIP__", 0, src, label)
            log.info("標籤對齊 %s：%s → __SKIP__（白名單外欄位）", sid, key)
            continue

        target = LABEL_MAP.get(text)
        if target and target != key:
            decisions[sid] = Decision(target, ordinal, src, label)
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
        sids.sort(key=lambda x: (by_id[x].loc["col"],
                                 by_id[x].loc.get("para_in_cell", 0)))
        wanted = ([f"{section}[].period"] if len(sids) == 1 else
                  [f"{section}[].start"] +
                  ["__SKIP__"] * (len(sids) - 2) + [f"{section}[].end"])
        for sid, new_key in zip(sids, wanted):
            key, ordinal, src, label = decisions[sid]
            # 錨定引擎的合併標題會讓同列兩格拿到同一個 period，這裡拆回起訖
            if src in ("model", "rule") and key != new_key:
                decisions[sid] = Decision(new_key, ordinal, src, label)
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
            # 一列可能並排好幾筆（家庭成員常印成「姓名 稱謂 年齡 職業」兩組）。
            # _renumber 已經照閱讀順序給過序號，這裡保留同列內的相對位移：
            # 這一列指派到第 n 筆，右邊那組就是第 n+1 筆
            base = min(decisions[sid].ordinal for sid in sids)
            for sid in sids:
                key, ordinal, src, label = decisions[sid]
                got = entry + (ordinal - base) if entry >= 0 else -1
                if got < 0 or got >= len(entries):
                    decisions[sid] = Decision("__SKIP__", 0, src, label)
                else:
                    decisions[sid] = Decision(key, got, src, label)
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
    result = llm.ask(host, ASSIGN_PROMPT, "\n".join(lines), schema,
                     model=model, label=f"列指派:{section}")
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
        groups.setdefault((slot.loc["table"], key), []).append(
            (slot.loc["row"], slot.loc["col"], sid))

    # 第幾筆照版面的閱讀順序：先由上而下，同一列再由左而右。
    # 同一列印兩組「姓名 稱謂 年齡 職業」是兩位家人，不是同一位填兩次；
    # 同一格的起訖兩個位置（「自　年　月」「至　年　月」）則屬於同一筆
    for items in groups.values():
        rank: Dict[Tuple[int, int], int] = {}
        for row, col, _sid in sorted(items):
            rank.setdefault((row, col), len(rank))
        for row, col, sid in items:
            decisions[sid] = decisions[sid]._replace(ordinal=rank[(row, col)])


def build_plan(slots: List[Slot], profile: Dict[str, Any],
               decisions: Dict[str, Decision]) -> Tuple[List[FillOp], List[FillOp]]:
    by_id = {s.id: s for s in slots}
    ops: List[FillOp] = []
    skipped: List[FillOp] = []

    for sid, (key, ordinal, source, label) in decisions.items():
        slot = by_id.get(sid)
        if slot is None:
            continue

        reason = _reject(key)
        if reason:
            skipped.append(FillOp(slot, key, "", source, label, reason, ordinal))
            continue

        value = get_value(profile, key, ordinal)
        if value in (None, ""):
            skipped.append(FillOp(slot, key, "", source, label,
                                  "個人資料中此欄位為空", ordinal))
            continue

        clear: Tuple[str, ...] = ()
        if slot.kind == "checkbox":
            picked = _pick_option(slot.options, str(value))
            if not picked:
                skipped.append(FillOp(slot, key, str(value), source, label,
                                      "勾選選項對不上", ordinal))
                continue
            # 同一格可能印了兩組選項（「婚姻：□單身 □已婚  兵役：□役畢 □免役」），
            # 只還原這個欄位自己的其他選項，否則填婚姻會把兵役的勾一起清掉
            others = {_pick_option(slot.options, c) for c in BY_KEY[key].choices}
            clear = tuple(o for o in others if o and o != picked)
            value = picked

        ops.append(FillOp(slot, key, str(value), source, label,
                          ordinal=ordinal, clear=clear))

    ops.sort(key=lambda o: o.slot.id)
    skipped.sort(key=lambda o: o.slot.id)

    # 「個人資料為空」是正常情形（表格列數多於實際學經歷），其餘代表判斷有疑慮
    for s in skipped:
        emit = log.debug if s.note == "個人資料中此欄位為空" else log.warning
        emit("略過 %s → %s：%s", s.slot.id, s.field_key, s.note)
    return ops, skipped


def _reject(key: str) -> str:
    if key in ("__SKIP__", "__UNKNOWN__"):
        return "模型判定非可填欄位或找不到對應"
    if key not in BY_KEY:
        return "此欄位已不存在，請重新指定"
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
        for alt in _alternatives(_squash(o)):
            if alt == target:
                return o
            if len(alt) >= 2 and len(target) >= 2 and (alt in target or target in alt):
                return o
    return None


def _alternatives(squashed: str) -> List[str]:
    for group in OPTION_SYNONYMS:
        words = [_squash(g) for g in group]
        if squashed in words:
            return words
    return [squashed]


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
    return ""


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

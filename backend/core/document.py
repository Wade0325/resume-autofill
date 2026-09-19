"""docx 的讀寫基礎：把文件攤成文字，並列出可以填字的位置。

這裡刻意不判斷任何一格「是什麼欄位」——那是模型的工作。
本模組只回答兩件機械性的問題：文件寫了什麼、哪些位置可以寫字、寫在哪個座標。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from docx import Document
from docx.document import Document as _Doc
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"^[\s　_＿…．\.\-—–]*$")
BLANK_RUN_RE = re.compile(r"[_＿]{2,}|[\.．]{4,}")
CHECKBOX_CHARS = "□☐▢◻"   # 不含 ○〇◯：中文常用來遮蔽名稱（○○公司）
CHECKED_CHARS = "■☑▣◼"
TRAILING_COLON_RE = re.compile(r"[:：□][ 　]*$")
# 印好的字之間留出來的書寫空間：「自    年    月」「血型：    型」
# 「公分/     公斤」的值都是寫在這些空白上。
#
# 這裡刻意只認「插不插得下字」這件機械事實，不猜那格是什麼欄位——
# 日期、身高、血型各寫一條正規表示式是永遠追不完的，那是模型的工作。
# 兩個半形空白以上或任一個全形空白才算：「就 學 期 間」那種單一空格
# 是字距排版，不是留白。
GAP_RE = re.compile(r"[ ]{2,}|　+")

# 表格要民國年：這個位置前面緊接著「民國」（「民國＿＿年」「出生日期（民國）：＿」）。
# 「中華民國」是國籍，不算——「國籍：中華民國　出生日期：＿年」要的是西元
ROC_BEFORE_RE = re.compile(r"(?<!中華)民國[\s)）\]］:：]*\Z")
ROC_WORD_RE = re.compile(r"(?<!中華)民國")


def to_roc(value: str) -> str:
    """值裡的西元年換成民國年：1996年3月15日 → 85年3月15日、
    2016年9月~2020年6月 → 105年9月~109年6月。只換後面接著年月分隔的四位數年份。"""
    return re.sub(r"(?<!\d)(19[1-9]\d|20\d\d)(?=\s*[年/.\-])",
                  lambda m: str(int(m.group(1)) - 1911), value)


@dataclass
class Slot:
    """一個可以填字的位置。"""
    id: str
    kind: str                                   # sdt | formfield | cell | inline | checkbox | print
    loc: Dict[str, Any] = field(default_factory=dict)    # writer 用來定位
    options: List[str] = field(default_factory=list)     # 勾選題的選項
    existing: str = ""                                   # 目前的內容，非空代表會被覆蓋

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def iter_block_items(doc: _Doc) -> Iterator[Any]:
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def cell_text(cell: _Cell) -> str:
    return "\n".join(p.text for p in cell.paragraphs).strip()


def is_blank(text: str) -> bool:
    return bool(PLACEHOLDER_RE.match(text or ""))


# 整段都被括號包起來的字是「寫法說明」，不是欄位名稱：
# 戶籍地址右邊那格印著「(請註明里、鄰)」，人就是寫在那格裡
ANNOTATION_RE = re.compile(r"^[（(][^（()）]*[)）]$")


def is_annotation(text: str) -> bool:
    return bool(ANNOTATION_RE.match((text or "").strip()))


def has_room(text: str) -> bool:
    """這段印好的字裡插不插得下值。純機械判斷，寫入端的前提條件：
    字與字之間有留白（「自　　年　　月」），或結尾是冒號、方框
    （「備註：」「郵遞區號□□□」），或整段只是括號說明——值接在後面。"""
    return bool(GAP_RE.search(text or "") or TRAILING_COLON_RE.search(text or "")
                or is_annotation(text))


def squash(text: str) -> str:
    """比對用：抹掉空白差異。"""
    return re.sub(r"[\s　]+", "", text or "")


def checkbox_options(text: str) -> List[str]:
    """從「□男　□女」取出選項文字。"""
    marks = CHECKBOX_CHARS + CHECKED_CHARS
    parts = re.split(f"[{marks}]", text)[1:]
    out = []
    for part in parts:
        name = re.split(r"[\s　,，、/／]+", part.strip())[0].strip(" :：()（）")
        if name:
            out.append(name)
    return out


class ParsedDoc:
    """一份 docx 只解析一次。

    分析流程要用到全文、可填位置、表格網格、列首欄首——各自重新
    Document(path)＋重算網格的話，範本快取命中（理應最快的路徑）時
    重複解析就是全部的延遲。python-docx 樹與攤平網格在這裡建一次，
    其餘都從同一份導出。只讀不寫；要改文件的 writer 自己另開一份。
    """

    def __init__(self, path: str):
        self.doc = Document(path)
        self._blocks = list(iter_block_items(self.doc))
        self.tables = [b for b in self._blocks if isinstance(b, Table)]
        self.paragraphs = [b for b in self._blocks if isinstance(b, Paragraph)]
        self.grids = [_grid(t) for t in self.tables]
        self._table_texts: Optional[List[List[List[str]]]] = None

    def table_texts(self) -> List[List[List[str]]]:
        """每張表格攤平後的文字網格 [表][列][欄]，合併儲存格在涵蓋的每格重複。

        給標籤錨定用：印著字的格子是標籤、可填位置在它右邊或下面。
        """
        if self._table_texts is None:
            self._table_texts = [[[cell_text(c) for c in row] for row in g]
                                 for g in self.grids]
        return self._table_texts

    def flatten(self, overwritable: Optional[Set[str]] = None) -> Tuple[str, List[Slot]]:
        """回傳（帶位置標記的全文, 位置清單）。

        空白的位置一律可填。已經有字的格子只有在 overwritable 裡才算可填——
        表格印好的欄位名稱與使用者填的值長得一樣（都是非空儲存格），
        差別只在後者是這個人的資料。那份清單由 reader 讀出來，所以判斷依據
        仍然是模型，不是規則。
        """
        overwritable = {squash(v) for v in (overwritable or set())}
        slots: List[Slot] = []
        lines: List[str] = []

        slots += _content_controls(self.doc)
        slots += _form_fields(self.doc)

        # 段落序號必須是 doc.paragraphs 的索引，writer 靠它定位
        table_index = 0
        para_index = 0
        for block in self._blocks:
            if isinstance(block, Paragraph):
                line = _paragraph_line(block, para_index, slots)
                if line:
                    lines.append(line)
                para_index += 1
            elif isinstance(block, Table):
                lines.append(f"[表格{table_index}]")
                lines += _table_lines(self.grids[table_index], table_index,
                                      slots, overwritable)
                table_index += 1

        by_kind: Dict[str, int] = {}
        for s in slots:
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        log.info("可填位置盤點 共%d個 %s", len(slots),
                 " ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
        for s in slots:
            # existing 對 cell 來說是使用者填的值，不能進 log；其餘種類的
            # existing 是表格印好的字（選項、格式），是版面不是個資
            shown = "" if s.kind == "cell" else s.existing.replace(chr(10), " ")[:28]
            log.debug("  位置 %-24s %-9s %s", s.id, s.kind, shown)
        return "\n".join(lines), slots

    def slot_headers(self, slots: List[Slot]) -> Dict[str, Dict[str, str]]:
        """每個位置的「列首」與「欄首」——同列往左、同欄往上第一格有字的內容。
        段落型位置（p6.b0 之類）沒有列欄概念，列首放空格前面印的那段字。

        這是機械抽取，不經過模型，所以可靠。用途：對映結果的標籤顯示、
        確定性標籤對齊、列指派——「列首＝高中/專科」比整份攤平全文精準得多。
        """
        by_table: Dict[int, List[Slot]] = {}
        for s in slots:
            if "table" in s.loc:
                by_table.setdefault(s.loc["table"], []).append(s)

        out: Dict[str, Dict[str, str]] = {}
        for ti, tslots in by_table.items():
            if ti >= len(self.tables):
                continue
            grid = self.grids[ti]
            texts = self.table_texts()[ti]
            for s in tslots:
                r, c = s.loc["row"], s.loc["col"]
                if r >= len(texts):
                    continue
                row_hdr = next(
                    (t for t in reversed(texts[r][:c]) if t.strip() and not is_blank(t)), "")
                col_hdr = ""
                for rr in range(r - 1, -1, -1):
                    if c < len(texts[rr]) and texts[rr][c].strip() and not is_blank(texts[rr][c]):
                        col_hdr = texts[rr][c]
                        break
                # 儲存格段落裡的底線：底線前面印的字（「…，原因」）比列首精準
                pic = s.loc.get("para_in_cell")
                if "blank_index" in s.loc and pic is not None:
                    row_hdr = _para_label(grid[r][c].paragraphs[pic].text, s)
                out[s.id] = {"row": row_hdr.replace("\n", " ")[:20],
                             "col": col_hdr.replace("\n", " ")[:20]}

        for s in slots:
            if "para" not in s.loc or s.loc["para"] >= len(self.paragraphs):
                continue
            out[s.id] = {"row": _para_label(self.paragraphs[s.loc["para"]].text, s), "col": ""}
        return out


# load() 加在可填位置上的 {{id}} 標記
MARKER_RE = re.compile(r"\{\{[^{}]*\}\}")

# 「使用者填過的值」的高置信長相。訊號刻意保守：
# 誤判成「有」只是多花一次模型呼叫，誤判成「沒有」會把已填值
# 當成表格印字（不覆蓋、還可能被當標籤），所以只認這幾種
_VALUE_HINT_RES = (
    re.compile(r"\d{7,}"),                       # 電話、身分證這種長數字串
    re.compile(r"[\w.+-]+@[\w-]+\.\w{2,}"),      # email
    re.compile(r"\d{4}\s*[年/.\-]\s*\d{1,2}"),   # 年月（2016/9、1998年3月）
    re.compile(r"\d{1,3},\d{3}"),                # 千分位金額（52,000）
    re.compile(f"[{CHECKED_CHARS}●]"),           # 已勾選的選項
)


def has_user_values(text: str) -> bool:
    """這份文件看起來有沒有使用者填過的值——決定要不要花一次模型呼叫
    去判讀既有內容（空白範本佔多數，那一步 15~44 秒完全是白等）。

    已知極端例外：整份只填了純中文（有姓名沒電話沒日期）的文件會被
    誤判為空白，那些值會被當成印字不覆蓋——履歷幾乎不可能長這樣，接受。
    """
    return any(r.search(text) for r in _VALUE_HINT_RES)


def text_only(path: str) -> str:
    """只要全文，不帶位置標記（匯入用）。

    標記若留著，模型抽值時會把 {{p6.tail}} 這種記號照抄成值，
    而逐字驗證比對的又是同一份帶標記的文字，攔不下來。
    """
    return MARKER_RE.sub("", ParsedDoc(path).flatten()[0])


def _para_label(text: str, slot: Slot) -> str:
    """段落型位置的標籤：這個空格（或勾選群）前面印的那段字。"""
    if slot.kind == "checkbox":
        head = re.split(f"[{CHECKBOX_CHARS}{CHECKED_CHARS}]", text)[0]
    else:
        blanks = list(BLANK_RUN_RE.finditer(text))
        bi = slot.loc.get("blank_index")
        if bi is not None and bi < len(blanks):
            start = blanks[bi - 1].end() if bi else 0
            head = text[start:blanks[bi].start()]
            # 方框以前的字是勾選群的標籤，不是這條底線的：
            # 「□退伍 □免役，原因＿＿」的底線該叫「免役，原因」，不是整串
            box = max(head.rfind(ch) for ch in CHECKBOX_CHARS + CHECKED_CHARS)
            head = head[box + 1:] if box >= 0 else head
        else:                       # pN.tail：結尾冒號型
            head = text
    return head.strip(" 　:：").replace("\n", " ")[-20:]


def fingerprint(slots: List[Slot]) -> str:
    """同一份表格 → 同一個指紋 → 直接沿用上次的對映，不必再問模型。"""
    payload = json.dumps(sorted((s.kind, s.id) for s in slots), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _content_controls(doc) -> List[Slot]:
    out = []
    for i, sdt in enumerate(doc.element.body.iter(qn("w:sdt"))):
        texts = [t.text or "" for t in sdt.iter(qn("w:t"))]
        out.append(Slot(id=f"sdt{i}", kind="sdt", loc={"sdt_index": i},
                        existing="".join(texts).strip()))
    return out


def _form_fields(doc) -> List[Slot]:
    out = []
    for i, ff in enumerate(doc.element.body.iter(qn("w:ffData"))):
        text_el = ff.find(qn("w:textInput"))
        default = ""
        if text_el is not None:
            default_el = text_el.find(qn("w:default"))
            if default_el is not None:
                default = default_el.get(qn("w:val"), "")
        out.append(Slot(id=f"ff{i}", kind="formfield", loc={"ff_index": i},
                        existing=default))
    return out


def _paragraph_line(para: Paragraph, index: int, slots: List[Slot]) -> str:
    text = para.text.strip()
    if not text:
        return ""
    return _para_render(text, f"p{index}", {"para": index}, slots)


def _para_render(text: str, sid: str, loc: Dict[str, Any], slots: List[Slot],
                 chk_text: Optional[str] = None,
                 chk_loc: Optional[Dict[str, Any]] = None,
                 room: bool = True) -> str:
    """一個段落的可填位置：勾選群一個，段落裡的每條底線各一個，
    都沒有但字裡留了空白時，那段印好的字自己就是一個位置。

    表格儲存格與文件段落共用——同樣是「一段字裡有方框和底線」，
    差別只在 loc 怎麼指到那個段落。

    「印著字又能填」只看插不插得下（has_room），不看那些字長什麼樣子：
    它是不是欄位、是哪個欄位、值該不該寫進去，一律由模型判。

    chk_text 覆寫勾選群的文字範圍：分行印的同一組選項（「□畢」「□肄」
    各佔一段）由呼叫端併成整組的文字，傳空字串代表這段是組員、
    位置已經由組長建過了。
    """
    text = text.strip()
    out, cursor = [], 0
    boxes = text if chk_text is None else chk_text
    options = checkbox_options(boxes) if any(
        ch in boxes for ch in CHECKBOX_CHARS + CHECKED_CHARS) else []
    if options:
        slots.append(Slot(id=f"{sid}.chk", kind="checkbox", loc=chk_loc or loc,
                          options=options, existing=boxes))
        out.append(f"{{{{{sid}.chk}}}} ")

    for bi, m in enumerate(BLANK_RUN_RE.finditer(text)):
        slots.append(Slot(id=f"{sid}.b{bi}", kind="inline",
                          loc={**loc, "blank_index": bi}))
        out.append(text[cursor:m.start()] + f"{{{{{sid}.b{bi}}}}}")
        cursor = m.end()

    if not out and room and has_room(text):
        note = {"annotation": True} if is_annotation(text) else {}
        slots.append(Slot(id=f"{sid}.txt", kind="print", loc={**loc, **note},
                          existing=text))
        return f"{text}{{{{{sid}.txt}}}}"
    return "".join(out) + text[cursor:]


def _table_lines(grid: List[List[_Cell]], table_index: int, slots: List[Slot],
                 overwritable: Set[str]) -> List[str]:
    out = []
    seen: Set[int] = set()   # 合併儲存格（含垂直合併）只處理一次，跨列去重
    for r, row in enumerate(grid):
        rendered = []
        for c, cell in enumerate(row):
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            rendered.append(_cell_render(cell, table_index, r, c, slots, overwritable))
        if any(x.strip() for x in rendered):
            out.append(" | ".join(rendered))
    return out


def _checkbox_groups(paras: List[str]) -> Dict[int, List[int]]:
    """儲存格裡的勾選段落怎麼分組：組長段落 → 這組涵蓋的段落索引。

    「□畢」「□肄」各佔一段其實是同一題。拆成兩個位置的話，錨定只認得到
    其中一個（同一格只留得下一筆），勾選也對不上——值「畢」比不到只印著
    「肄」的那格。方框前面印了字的段落自成一組，所以一格六道是非題
    （「您是否…？□是 □否」各佔一段）不會被併起來。
    """
    marks = CHECKBOX_CHARS + CHECKED_CHARS
    groups: Dict[int, List[int]] = {}
    leader: Optional[int] = None
    for i, text in enumerate(paras):
        if not any(ch in text for ch in marks):
            leader = None                    # 中間夾了別的內容就斷開
            continue
        head = re.split(f"[{marks}]", text)[0].strip(" 　:：")
        if head or leader is None:
            leader = i
            groups[i] = [i]
        else:
            groups[leader].append(i)
    return groups


def _cell_render(cell: _Cell, table_index: int, r: int, c: int,
                 slots: List[Slot], overwritable: Set[str]) -> str:
    text = cell_text(cell)
    sid = f"tbl{table_index}.r{r}.c{c}"
    loc = {"table": table_index, "row": r, "col": c}

    if is_blank(text):
        slots.append(Slot(id=sid, kind="cell", loc=loc))
        return f"{{{{{sid}}}}}"

    # 使用者填過的值：整格換掉，不是插字
    if squash(text) in overwritable:
        slots.append(Slot(id=sid, kind="cell", loc=loc, existing=text))
        return f"{{{{{sid}}}}}{text}".replace("\n", " ")

    paras = [p.text.strip() for p in cell.paragraphs]
    # 這一段已經寫著使用者的值（「民國 87 年 3 月 25 日」）就不再插字：
    # 插進去會變成「民國 1998 87 0 年」，把原本正確的資料弄壞。
    # 整格值的覆蓋是上面那條路（kind=cell），這裡處理的是標籤與值混在一段的格子
    written = [any(v and v in squash(x) for v in overwritable) for x in paras]
    if any(paras):
        # 印著字的格子逐段處理，跟一般段落同一套規則：每段的勾選群是一個位置，
        # 段落裡的底線各自也是一個位置，字裡留了空白的那段自己是一個位置。
        # 一格印六道是非題（各佔一段）時才勾得到每一題；
        # 「□免役，原因＿＿」的底線才填得進去。
        groups = _checkbox_groups(paras)
        in_group = {i for idxs in groups.values() for i in idxs}
        rendered = []
        for i, ptext in enumerate(paras):
            idxs = groups.get(i)
            chk_text = chk_loc = None
            if idxs and len(idxs) > 1:        # 分行印的同一組選項，併成一個位置
                chk_text = "\n".join(paras[j] for j in idxs)
                chk_loc = {**loc, "para_in_cell": i, "chk_paras": idxs}
            elif idxs is None and i in in_group:
                chk_text = ""                 # 組員：位置已經由組長建過了
            rendered.append(_para_render(ptext, f"{sid}.p{i}",
                                         {**loc, "para_in_cell": i}, slots,
                                         chk_text, chk_loc, not written[i]))
        if any("{{" in x for x in rendered):
            return " ".join(rendered).replace("\n", " ")

    return text.replace("\n", " ")


def _grid(table: Table) -> List[List[_Cell]]:
    """把合併儲存格攤平成矩形網格，同一個 cell 會在它涵蓋的每個座標出現。

    垂直合併的延續格映射回上一列的主格：延續格的 XML 內容 Word 不渲染，
    直接讀會誤判成空白可填位置，寫進去的字也永遠看不見。
    """
    rows: List[List[_Cell]] = []
    for row in table.rows:
        cells: List[_Cell] = []
        for tc in row._tr.tc_lst:
            span = tc.tcPr.find(qn("w:gridSpan")) if tc.tcPr is not None else None
            width = int(span.get(qn("w:val"))) if span is not None else 1
            vmerge = tc.tcPr.find(qn("w:vMerge")) if tc.tcPr is not None else None
            if (vmerge is not None
                    and vmerge.get(qn("w:val"), "continue") != "restart"
                    and rows and len(rows[-1]) > len(cells)):
                cell = rows[-1][len(cells)]
            else:
                cell = _Cell(tc, table)
            cells.extend([cell] * width)
        rows.append(cells)
    return rows

"""
docx 寫回器 (Format-preserving Writer)
------------------------------------------------
最大的坑：Word 會把一句話拆成好幾個 <w:r> run（拼字檢查、修訂 ID 都會造成切割），
所以「你看得到的字串」在 XML 裡往往不是連續的。直接用 cell.text = "..." 會把
儲存格內所有格式（字型、大小、置中）一次清光，出來的履歷會很醜。

這裡的作法：位置一律用段落文字（para.text）的字元位置算，寫入交給 runs.write_changes
（與看版面共用）——只改牽涉到的 <w:t>，run 裡的勾選符號、圖片、功能變數一律不碰；
新字沿用原本那個 run 的格式，空白格則沿用段落標記的格式、沒設就借同一列的。

highlight 模式會把填入的字加上黃色底色，只給網頁預覽用；下載的成品一律不標。
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from docx import Document
from docx.oxml.ns import qn

from .document import (
    BLANK_RUN_RE,
    CHECKBOX_CHARS,
    CHECKED_CHARS,
    GAP_RE,
    ROC_BEFORE_RE,
    TRAILING_COLON_RE,
    _grid,
    to_roc,
)
from .runs import write_changes

log = logging.getLogger(__name__)

# 只列 CHECKBOX_CHARS 裡的字元——document.py 刻意把 ○〇◯ 排除在方框之外
CHECK_MAP = {"□": "■", "☐": "☑", "▢": "■", "◻": "◼"}
# 不能用 CHECK_MAP 反轉：□ 與 ▢ 都對應到 ■，反轉時會挑到 ▢，
# 還原出來的框就跟原本長得不一樣了
UNCHECK_MAP = {"■": "□", "☑": "☐", "◼": "◻"}


def _write_into_cell(grid: List[List[Any]], row: int, col: int,
                     text: str, highlight: bool) -> bool:
    """整格換成 text（第一段）。格子裡的圖片、勾選符號不會被一起清掉。"""
    if row >= len(grid) or col >= len(grid[row]):
        return False
    cell = grid[row][col]
    para = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
    write_changes(para, [(0, len(para.text), text)], highlight)
    return True


def _replace_span(para, start: int, end: int, text: str, highlight: bool) -> bool:
    """把段落文字的 [start, end) 區間換成 text，只動到牽涉到的字。
    start 超過字尾就是純追加：「可到職日：」後面直接接上。"""
    total = len(para.text)
    write_changes(para, [(min(start, total), min(end, total), text)], highlight)
    return True


def _fill_inline(para, text: str, highlight: bool, blank_index: int = 0) -> bool:
    """處理『姓名：______』『… 關係：___ 電話：___』或『可到職日：』結尾。"""
    full = para.text
    blanks = list(BLANK_RUN_RE.finditer(full))
    if blanks:
        if blank_index >= len(blanks):
            return False
        m = blanks[blank_index]
        return _replace_span(para, m.start(), m.end(), text, highlight)
    m = re.search(r"[:：][ \u3000]*$", full)
    if m:
        return _replace_span(para, m.end(), len(full), text, highlight)
    return _replace_span(para, len(full), len(full), text, highlight)


TOKEN_RE = re.compile(r"\d+|[^\W\d_]+", re.UNICODE)


def _fill_print(para, value: str, highlight: bool) -> bool:
    """把值插進印好的字裡留的空白：
    「自    年    月」＋2016/9 →「自 2016 年 9 月」、
    「血型：     型」＋B →「血型： B 型」、「備註：」＋xxx →「備註： xxx」。

    兩種放法，先試對得上的那種：
    1. 疊合——印的字照順序整串出現在值裡（「年  月  日」對上
       「1998年03月25日」），就把值夾在中間的字補到對應的位置去。
       單位對單位，不會錯位。
    2. 依序——印好的字先從值裡扣掉（版面上已經有了），剩下的切成片段，
       一段一個空白，多出來的併進最後一段。
    一個空白都沒有（「備註：」）就接在字尾。
    """
    text = para.text
    # 「…英文名：」這種結尾冒號是最明確的下筆位置，優先用；
    # 一段裡有好幾組「標籤：值」時，前面的空白早就填過別人的值了
    gaps = ([] if TRAILING_COLON_RE.search(text)
            else [(m.start(), m.end()) for m in GAP_RE.finditer(text)])
    if not gaps:
        return _replace_span(para, len(text), len(text), f" {value}", highlight)

    # 「民國　年　月　日」：值存的是西元，第一格前面印著民國就換成民國年
    # （以前寫出「民國 1996 年」）
    if ROC_BEFORE_RE.search(text[:gaps[0][0]]):
        value = to_roc(value)
    spans = _overlay(text, gaps, value) or _spread(text, gaps, value)
    done = False
    for start, end, new in reversed(spans):
        done |= _replace_span(para, start, end, new, highlight)
    return done


def _fixed_parts(text: str, gaps: List[Tuple[int, int]]) -> List[Tuple[int, int, str]]:
    """印好的字被空白切成幾段，回傳每段的 (起, 訖, 內容)。"""
    out, cursor = [], 0
    for start, end in gaps + [(len(text), len(text))]:
        chunk = text[cursor:start]
        if chunk.strip():
            out.append((cursor, start, chunk.strip()))
        cursor = end
    return out


def _overlay(text: str, gaps: List[Tuple[int, int]], value: str
             ) -> Optional[List[Tuple[int, int, str]]]:
    """印的字整串照順序出現在值裡 → 兩邊疊合。對不上回 None。"""
    spans, pos = [], 0
    for start, end, chunk in _fixed_parts(text, gaps):
        found = value.find(chunk, pos)
        if found < 0:
            return None
        piece = value[pos:found].strip()
        pos = found + len(chunk)
        if not piece:
            continue
        gap = next((g for g in gaps if g[1] == start), None)
        # 這段字前面沒有空白可用時，連它一起換掉（零寬度的區間寫不進去）
        spans.append((gap[0], gap[1], f" {piece} ") if gap
                     else (start, end, f"{piece}{chunk}"))
    return spans


def _spread(text: str, gaps: List[Tuple[int, int]], value: str
            ) -> List[Tuple[int, int, str]]:
    """值切成片段，依序放進每個空白。"""
    rest = value
    for _s, _e, chunk in _fixed_parts(text, gaps):
        rest = rest.replace(chunk, " ", 1)      # 版面上已經印著的字不用再填一次
    tokens = TOKEN_RE.findall(rest)
    if not tokens:
        return []
    if len(tokens) > len(gaps):                 # 多出來的併進最後一格
        tokens = tokens[:len(gaps) - 1] + ["".join(tokens[len(gaps) - 1:])]
    return [(start, end, f" {tok} ")
            for (start, end), tok in zip(gaps, tokens) if tok]


def _option_box(text: str, option: str) -> Optional[int]:
    """緊接在這個選項前面的方框位置（已勾未勾都算）。

    要一路找到「前面真的有方框」的那次出現為止：選項字常常也出現在題目裡
    （「您是否曾…？ □是 □否」的「是否」），只看第一次出現會定位到題目上。
    """
    start = 0
    while True:
        idx = text.find(option, start)
        if idx < 0:
            return None
        for j in range(idx - 1, max(-1, idx - 4), -1):
            if text[j] in CHECKBOX_CHARS or text[j] in CHECKED_CHARS:
                return j
        start = idx + 1


def _clear_boxes(para, options: Sequence[str]) -> None:
    """把同組其他選項已經勾著的框還原。

    範本不一定是空白的——公司先勾好、或使用者上傳自己填過的履歷都很常見，
    不還原舊的就會變成「■無 ■有」兩個都勾。
    """
    for other in options:
        p = _option_box(para.text, other)
        if p is not None and para.text[p] in CHECKED_CHARS:
            _replace_span(para, p, p + 1, UNCHECK_MAP.get(para.text[p], "□"), False)


def _fill_checkbox(para, option: str, highlight: bool) -> bool:
    """勾掉這個選項。目標本來就勾對時也要算成功，
    否則會回報成「有 N 格沒填上」的假警報。"""
    pos = _option_box(para.text, option)
    if pos is None:
        return False
    if para.text[pos] in CHECKED_CHARS:
        return True
    return _replace_span(para, pos, pos + 1, CHECK_MAP.get(para.text[pos], "■"), highlight)


def _fill_sdt(sdts: List[Any], index: int, text: str) -> bool:
    if index >= len(sdts):
        return False
    sdt = sdts[index]
    pr = sdt.find(qn("w:sdtPr"))
    if pr is not None:                       # 清掉「按此輸入」的預留位置狀態
        for ph in pr.findall(qn("w:showingPlcHdr")):
            pr.remove(ph)
    content = sdt.find(qn("w:sdtContent"))
    if content is None:
        return False
    tnodes = list(content.iter(qn("w:t")))
    if tnodes:
        tnodes[0].text = text
        for t in tnodes[1:]:
            t.text = ""
        return True
    return False


def _fill_formfield(ffs: List[Any], index: int, text: str) -> bool:
    if index >= len(ffs):
        return False
    run = ffs[index].getparent().getparent()          # ffData -> fldChar -> r
    parent = run.getparent()
    children = list(parent)
    try:
        i = children.index(run)
    except ValueError:
        return False
    state = "begin"
    for node in children[i + 1:]:
        fld = node.find(qn("w:fldChar")) if node.tag == qn("w:r") else None
        if fld is not None:
            ftype = fld.get(qn("w:fldCharType"))
            if ftype == "separate":
                state = "result"
                continue
            if ftype == "end":
                break
        if state == "result" and node.tag == qn("w:r"):
            tnodes = list(node.iter(qn("w:t")))
            if tnodes:
                tnodes[0].text = text
                for t in tnodes[1:]:
                    t.text = ""
                return True
    return False


def _kept(before: str, after: str) -> bool:
    """印好的字有沒有原封不動地留著（只准插入，不准改寫）。

    跟匯入端「值必須逐字出現在原文」是同一套紀律，方向相反：
    那邊防模型編造內容，這邊防我們寫壞表格。
    """
    it = iter(after)
    return all(ch in it for ch in before if not ch.isspace())


def _target_paras(doc, grid_of, loc: Dict[str, Any]) -> List[Any]:
    """這個位置涵蓋的段落。分行印的勾選群（「□畢」「□肄」）橫跨好幾段，
    要勾的那個選項不一定在組長那一段。"""
    if "table" not in loc:
        return [doc.paragraphs[loc["para"]]]
    cell = grid_of(loc["table"])[loc["row"]][loc["col"]]
    idxs = loc.get("chk_paras")
    if idxs is None:
        i = loc.get("para_in_cell")
        return list(cell.paragraphs) if i is None else [cell.paragraphs[i]]
    return [cell.paragraphs[i] for i in idxs if i < len(cell.paragraphs)]


def apply_ops(src_path: str, out_path: str, ops: List[Any],
              highlight: bool = False) -> Dict[str, Any]:
    doc = Document(src_path)
    ok, fail = [], []

    # 每個 op 都重攤網格／重掃全文的話，成本是 O(op 數×表格大小)。
    # 這裡的寫入只動 run 與文字節點、不動表格結構，快取是安全的。
    grids: Dict[int, List[List[Any]]] = {}
    scans: Dict[str, List[Any]] = {}

    def grid_of(ti: int) -> List[List[Any]]:
        if ti not in grids:
            grids[ti] = _grid(doc.tables[ti])
        return grids[ti]

    def scan_of(tag: str) -> List[Any]:
        if tag not in scans:
            scans[tag] = list(doc.element.body.iter(qn(tag)))
        return scans[tag]

    # 同一段落有多個填空時，必須由後往前寫，
    # 否則填完第 1 個空格後，後面空格的字元位置就跑掉了。
    ops = sorted(ops, key=lambda o: -o.slot.loc.get("blank_index", 0))

    for op in ops:
        slot = op.slot
        loc = slot.loc
        kind = slot.kind
        done = False
        try:
            if kind == "sdt":
                done = _fill_sdt(scan_of("w:sdt"), loc["sdt_index"], op.value)
            elif kind == "formfield":
                done = _fill_formfield(scan_of("w:ffData"), loc["ff_index"], op.value)
            elif kind == "cell":
                done = _write_into_cell(grid_of(loc["table"]), loc["row"], loc["col"],
                                        op.value, highlight)
            elif kind == "inline":
                if "table" in loc:
                    cell = grid_of(loc["table"])[loc["row"]][loc["col"]]
                    para = cell.paragraphs[loc["para_in_cell"]]
                else:
                    para = doc.paragraphs[loc["para"]]
                done = _fill_inline(para, op.value, highlight, loc.get("blank_index", 0))
            elif kind == "print":
                para = _target_paras(doc, grid_of, loc)[0]
                before, snap = para.text, copy.deepcopy(para._p)
                done = _fill_print(para, op.value, highlight)
                # 只准插入：印好的字必須一個不少地照原順序留著。對不上就整段換回
                # 寫之前的樣子（連格式）——寧可留白讓人手寫，也不能把表格印的字寫壞
                if done and not _kept(before, para.text):
                    para._p[:] = list(snap)
                    done = False
            elif kind == "checkbox":
                paras = _target_paras(doc, grid_of, loc)
                for p in paras:      # 分行印的一組，舊的勾可能在別段，先全部還原
                    _clear_boxes(p, op.clear)
                done = any(_fill_checkbox(p, op.value, highlight) for p in paras)
        except Exception as e:                    # 單一格失敗不影響其他欄位
            fail.append({"slot": slot.id, "error": str(e)})
            continue
        # 值是個資，只記長度；欄位代碼與位置代碼可以記
        log.debug("  寫入 %-24s %-9s %-26s %d字 → %s",
                  slot.id, kind, op.field_key, len(str(op.value)),
                  "成功" if done else "定位失敗")
        (ok if done else fail).append(
            {"slot": slot.id, "field": op.field_key}
            if done else {"slot": slot.id, "error": "定位失敗"})

    doc.save(out_path)
    return {"written": len(ok), "failed": len(fail), "ok": ok, "fail": fail}

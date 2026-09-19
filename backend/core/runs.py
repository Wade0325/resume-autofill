"""段落文字的就地改寫：只換字，run 裡的其他東西原封不動。

讀文字（writer.py）與看版面（filler.py）兩個填寫引擎共用。以前兩邊各自用
python-docx 的 run.text= 整段重寫，那會連 run 的子元素一起清掉：Wingdings 勾選框
（w:sym）、畫出來的方框、功能變數、圖片跟著不見，底線與字型也只剩第一個 run 的。
"""
from __future__ import annotations

import copy
import re
from typing import Any, List, Tuple

from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.run import Run

# para.text 由這幾種元素組成（python-docx 的 CT_R.text），位置要照同一套算法對回去
_TEXT_TAGS = {qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr"), qn("w:noBreakHyphen"),
              qn("w:ptab")}
_W_T = qn("w:t")
# 借來的格式不帶這些：修訂標記放到 run 上不合法；刪除線、隱藏是那個空段落（或借來的那個
# run）自己的事——富邦「專科」那列的段落標記帶著刪除線，照抄的話填進去的學校名稱整個被劃掉
_NOT_INHERITED = {qn(f"w:{tag}") for tag in (
    "ins", "del", "moveFrom", "moveTo", "rPrChange",
    "strike", "dstrike", "vanish", "specVanish", "webHidden")}


def _atoms(p) -> List[Tuple[Any, int, int]]:
    """段落裡的每個文字元素與它在 para.text 的 [起, 訖)。
    跟 para.text 同一套組法：段落直屬的 run 與超連結裡的 run，照文件順序。"""
    out, pos = [], 0
    for r in p.xpath("./w:r | ./w:hyperlink/w:r"):
        for el in r:
            if el.tag in _TEXT_TAGS:
                n = len(str(el))
                out.append((el, pos, pos + n))
                pos += n
    return out


def _new_t(text: str):
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")      # 前後的空白才不會被 Word 吃掉
    return t


def _pieces(text: str) -> List[Any]:
    """新字換成 run 的子元素：換行 → w:br、Tab → w:tab，其餘是 w:t。"""
    out = []
    for piece in re.split(r"(\n|\t)", text):
        if piece == "\n":
            out.append(OxmlElement("w:br"))
        elif piece == "\t":
            out.append(OxmlElement("w:tab"))
        elif piece:
            out.append(_new_t(piece))
    return out


def _twin(r, para, highlight: bool):
    """同格式的空 run。highlight 標黃底（走 python-docx，rPr 裡的順序才合規定）。"""
    twin = OxmlElement("w:r")
    rpr = r.find(qn("w:rPr"))
    if rpr is not None:
        twin.append(copy.deepcopy(rpr))
    if highlight:
        Run(twin, para).font.highlight_color = WD_COLOR_INDEX.YELLOW
    return twin


def _blank_rpr(p):
    """空段落要新開 run 時用的格式：跟 Word 一樣沿用段落標記的格式；
    段落標記沒設，就借同一列有字的 run 的格式（原本標楷體的表格才不會填出新細明體）。"""
    def inherit(rpr):
        rpr = copy.deepcopy(rpr)
        for el in [el for el in rpr if el.tag in _NOT_INHERITED]:
            rpr.remove(el)
        return rpr

    mark = p.find(f"{qn('w:pPr')}/{qn('w:rPr')}")
    if mark is not None:
        return inherit(mark)
    row = next(p.iterancestors(qn("w:tr")), None)
    for r in (row.iter(qn("w:r")) if row is not None else ()):
        rpr = r.find(qn("w:rPr"))
        if rpr is not None and "".join(t.text or "" for t in r.iter(_W_T)).strip():
            return inherit(rpr)
    return None


def write_changes(para, changes: List[Tuple[int, int, str]], highlight: bool = False) -> None:
    """把 (起, 訖, 新字) 寫進段落，只動牽涉到的文字，run 裡其他東西原封不動。

    位置是 para.text 的字元位置。由右往左一段一段換，前面的位置才不會跑掉；
    新字放進原本那個 run、沿用它的格式——底線上的空格填完還是有底線。
    highlight（網頁預覽）把新字拆成自己的 run 標黃底，其餘不變。
    """
    p = para._p
    for start, end, rep in sorted(changes, reverse=True):
        atoms = _atoms(p)
        if not atoms:                       # 空段落（空白格）：新開一個 run
            r = OxmlElement("w:r")
            rpr = _blank_rpr(p)
            if rpr is not None:
                r.append(rpr)
            r.extend(_pieces(rep))
            p.append(r)
            if highlight:
                Run(r, para).font.highlight_color = WD_COLOR_INDEX.YELLOW
            continue
        texts = [a for a in atoms if a[0].tag == _W_T]
        if start == end:                    # 純插入：優先接在前面那段字的尾巴
            target = (next((a for a in reversed(texts) if a[2] == start), None)
                      or next((a for a in texts if a[1] <= start < a[2]), None))
        else:
            # 新字放進佔了這段最多字的那個：「民國 ＿＿年」的留白是一個普通空格加四個
            # 底線空格，放進前面那個空格的 run，數字就不在底線上了
            target = max((a for a in texts if a[1] < end and a[2] > start),
                         key=lambda a: min(end, a[2]) - max(start, a[1]), default=None)
        if target is None:                  # 附近沒有 w:t（前後只有 Tab 之類）：在該處補一個空的
            before = [a for a in atoms if a[2] <= start]
            t = _new_t("")
            if before:
                before[-1][0].addnext(t)
            else:
                atoms[0][0].addprevious(t)
            target = (t, start, start)
        # 範圍內其他元素的字拿掉（被換掉的那段字本來就包含它們）。不佔字的元素
        # （分頁、分欄的 w:br）不在 para.text 裡，不算被換掉的字，留著
        for el, s, e in atoms:
            if el is target[0] or s == e or not (s < end and e > start):
                continue
            if el.tag == _W_T:
                a, b = max(start, s) - s, min(end, e) - s
                el.text = (el.text or "")[:a] + (el.text or "")[b:]
                if el.text:
                    el.set(qn("xml:space"), "preserve")
                else:
                    el.getparent().remove(el)
            else:
                el.getparent().remove(el)

        el, s, e = target
        text = el.text or ""
        a, b = max(start, s) - s, min(end, e) - s
        head, tail = text[:a], text[b:]
        r = el.getparent()
        if not highlight:
            for piece in ([_new_t(head)] if head else []) + _pieces(rep) + (
                    [_new_t(tail)] if tail else []):
                el.addprevious(piece)
            r.remove(el)
            continue
        # 預覽：原本的 run 留前半段，新字一個 run（黃底），後半段與後面的元素另一個 run
        after = list(r)[list(r).index(el) + 1:]
        if head:
            el.addprevious(_new_t(head))
        r.remove(el)
        mid = _twin(r, para, highlight=True)
        mid.extend(_pieces(rep))
        r.addnext(mid)
        if tail or after:
            rest = _twin(r, para, highlight=False)
            if tail:
                rest.append(_new_t(tail))
            rest.extend(after)              # 搬過去，順序不變
            mid.addnext(rest)

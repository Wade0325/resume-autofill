"""左右對照預覽：把 docx 攤成帶座標的結構化區塊給前端渲染。

座標規則必須跟 document.py 一模一樣（tblN.rR.cC、pN.bB、pN.tail、pN.chk），
前端才能拿 plan 的 slot_id 對回每一格。所以這裡不自己「偵測」位置——
位置清單由呼叫端從 job 的 anchors 給，這裡只負責把文件攤開、
在算出來的座標剛好是已知 slot 的地方做記號。

區塊格式（前端 DocPreview.tsx 依此渲染）：
  {"kind": "p",     "segs": [{"t": 文字} | {"t": 原文, "s": slot_id}]}
  {"kind": "table", "grid_cols": n,
   "rows": [{"cells": [{"colspan": n, "segs": [...]}]}]}
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from .document import (BLANK_RUN_RE, CHECKBOX_CHARS, CHECKED_CHARS,
                       TRAILING_COLON_RE, cell_text, iter_block_items)


def build(path: str, slot_ids: Set[str]) -> List[Dict[str, Any]]:
    doc = Document(path)
    blocks: List[Dict[str, Any]] = []
    table_index = 0
    para_index = 0
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            segs = _paragraph_segs(block, para_index, slot_ids)
            if segs:
                blocks.append({"kind": "p", "segs": segs})
            para_index += 1
        elif isinstance(block, Table):
            blocks.append(_table_block(block, table_index, slot_ids))
            table_index += 1
    return blocks


def _paragraph_segs(para: Paragraph, index: int,
                    slot_ids: Set[str]) -> List[Dict[str, str]]:
    text = para.text.strip()
    if not text:
        return []

    chk = f"p{index}.chk"
    if chk in slot_ids and any(ch in text for ch in CHECKBOX_CHARS + CHECKED_CHARS):
        return [{"t": text, "s": chk}]

    blanks = list(BLANK_RUN_RE.finditer(text))
    if blanks:
        segs, cursor = [], 0
        for bi, m in enumerate(blanks):
            sid = f"p{index}.b{bi}"
            if text[cursor:m.start()]:
                segs.append({"t": text[cursor:m.start()]})
            # 底線本身就是要被填掉的空格，原文留著讓左邊看得到
            segs.append({"t": m.group(), "s": sid} if sid in slot_ids
                        else {"t": m.group()})
            cursor = m.end()
        if text[cursor:]:
            segs.append({"t": text[cursor:]})
        return segs

    tail = f"p{index}.tail"
    if tail in slot_ids and TRAILING_COLON_RE.search(text):
        return [{"t": text}, {"t": "", "s": tail}]

    return [{"t": text}]


def _table_block(table: Table, table_index: int,
                 slot_ids: Set[str]) -> Dict[str, Any]:
    rows = []
    grid_cols = 0
    for r, row in enumerate(table.rows):
        cells = []
        col = 0
        for tc in row._tr.tc_lst:
            span = tc.tcPr.find(qn("w:gridSpan")) if tc.tcPr is not None else None
            width = int(span.get(qn("w:val"))) if span is not None else 1
            cell = _Cell(tc, table)
            sid = f"tbl{table_index}.r{r}.c{col}"
            text = cell_text(cell).replace("\n", " ")
            cells.append({
                "colspan": width,
                "segs": [{"t": text, "s": sid} if sid in slot_ids else {"t": text}],
            })
            col += width
        grid_cols = max(grid_cols, col)
        rows.append({"cells": cells})
    return {"kind": "table", "grid_cols": grid_cols, "rows": rows}

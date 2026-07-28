"""從已填寫的履歷讀出資料（匯入方向）。

跟 extractor 的差別很關鍵：

  extractor  是為了「填寫」——必須知道每個值要寫回文件的哪個精確座標，
             所以走結構解析，靠「往左找標籤、往上找表頭」來配對。
  reader     是為了「匯入」——只要拿到值，不需要座標。

結構配對在合併儲存格的寬表格上會錯。真實案例：16 欄的表格裡「聯絡電話」
被當成姓名的值、「主修科系」被當成學校名稱。與其繼續堆積結構規則，
不如把整份文件攤成文字交給模型讀——模型看到的是
「姓名 | 中文：郭韋德 英文：Wade | 聯絡電話 | (M)：0905380296」
這一整列，配對對它來說是常識。

防幻覺的手段是驗證而不是祈禱：模型回傳的每個值都必須在原文裡逐字找得到，
找不到就丟掉。模型只能「選取片段」，不能「編造內容」。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List

import requests
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .extractor import _grid, cell_text, iter_block_items
from .schema import FIELDS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是履歷資料抽取器。使用者會給你一份已經填寫完成的履歷全文，
請把各欄位的值抽出來。

規則：
1. 只能從原文「逐字照抄」，不可改寫、翻譯、換算、補字或推論。抄不到就不要輸出那個鍵。
2. 表格以 | 分隔，同一列中標籤與值通常相鄰，例如「姓名 | 郭韋德 | 聯絡電話 | 0912...」。
3. 空白格會顯示成 ␣，代表沒填，不要當成值。
4. 只抽「求職者本人填寫」的資料。由公司內部填寫的欄位（面談情形、任用與否、
   建議薪資、初試／複試日期、主管簽名欄）一律略過。
5. 勾選題請輸出被勾選的那一項（■ 或 ☑ 標記的），沒有勾就不要輸出。
6. education 與 experience 可能有多筆，依表格由上到下的順序輸出，不要輸出空白列。"""


# --------------------------------------------------------------------------
# 1) docx → 純文字
# --------------------------------------------------------------------------
def serialize(path: str) -> str:
    """把整份文件攤成一行一列的純文字，表格用 | 分隔。"""
    doc = Document(path)
    lines: List[str] = []
    table_index = 0

    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                lines.append(text)
        elif isinstance(block, Table):
            lines.append(f"[表格{table_index}]")
            lines.extend(_table_lines(block))
            table_index += 1

    return "\n".join(lines)


def _table_lines(table: Table) -> List[str]:
    out = []
    for row in _grid(table):
        cells, seen = [], set()
        for cell in row:
            if cell is None:
                continue
            # 合併儲存格會在多個座標回傳同一個物件，去重才不會整列重複
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(cell_text(cell).replace("\n", " ").strip() or "␣")
        if any(c != "␣" for c in cells):
            out.append(" | ".join(cells))
    return out


# --------------------------------------------------------------------------
# 2) 欄位 → JSON Schema
# --------------------------------------------------------------------------
def build_schema() -> Dict[str, Any]:
    """用 FIELDS 生出抽取用的 schema。沒有 required：抄不到的欄位就別輸出。"""
    props: Dict[str, Any] = {}
    rows: Dict[str, Dict[str, Any]] = {}

    for f in FIELDS:
        if "[]." in f.key:
            root, sub = f.key.split("[].", 1)
            rows.setdefault(root, {})[sub] = {"type": "string"}
        else:
            props[f.key] = {"type": "string"}

    for root, sub_props in rows.items():
        props[root] = {
            "type": "array",
            "items": {"type": "object", "properties": sub_props},
        }
    return {"type": "object", "properties": props}


def describe_fields() -> str:
    lines = []
    for f in FIELDS:
        extra = f"（選項：{'、'.join(f.choices)}）" if f.choices else ""
        lines.append(f"- {f.key}: {f.label}{extra}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 3) 呼叫模型 + 驗證
# --------------------------------------------------------------------------
def read_profile(text: str, host: str, model: str = "local",
                 timeout: int = 300) -> Dict[str, Any]:
    """回傳 {欄位代碼: 值}；列表欄位回傳 {root: [{sub: 值}]}。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"可抽取的欄位：\n{describe_fields()}\n\n履歷全文：\n{text}"},
        ],
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "resume_data", "strict": True,
                            "schema": build_schema()},
        },
    }

    t0 = time.perf_counter()
    r = requests.post(f"{host}/v1/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    elapsed = int((time.perf_counter() - t0) * 1000)
    content = choice["message"]["content"] or ""

    log.info("模型讀取履歷 全文=%d字 finish=%s 回應=%d字 耗時=%dms",
             len(text), choice.get("finish_reason"), len(content), elapsed)
    if choice.get("finish_reason") == "length" or not content:
        raise RuntimeError(
            f"模型未產生有效輸出（finish_reason={choice.get('finish_reason')}）")

    return _keep_verbatim(json.loads(content), text)


def _normalize(s: str) -> str:
    """比對用：抹掉空白差異。模型常常會把原文的多重空白壓成一個。"""
    return re.sub(r"[\s　]+", "", s)


def _keep_verbatim(data: Dict[str, Any], source: str) -> Dict[str, Any]:
    """只保留能在原文逐字找到的值——這是防幻覺的唯一防線。"""
    haystack = _normalize(source)
    kept: Dict[str, Any] = {}
    dropped: List[str] = []

    for key, value in data.items():
        if isinstance(value, list):
            rows = []
            for row in value:
                if not isinstance(row, dict):
                    continue
                clean = {k: v for k, v in row.items()
                         if isinstance(v, str) and v.strip()
                         and _normalize(v) in haystack}
                if clean:
                    rows.append(clean)
                dropped += [f"{key}[].{k}" for k, v in row.items()
                            if isinstance(v, str) and v.strip() and k not in clean]
            if rows:
                kept[key] = rows
        elif isinstance(value, str) and value.strip():
            if _normalize(value) in haystack:
                kept[key] = value.strip()
            else:
                dropped.append(key)

    if dropped:
        # 只記欄位代碼不記值——被丟掉的多半是模型改寫過的個資
        log.warning("捨棄非原文的抽取結果 欄位=%s", ",".join(sorted(set(dropped))))
    return kept

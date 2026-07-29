"""從已填寫的履歷讀出資料。

整份文件交給模型讀，不做任何結構配對——履歷表常用合併儲存格排版，
「標籤在左邊那一格」這種假設在實務上經常不成立。模型看到的是整列，
配對對它是常識。

防幻覺靠驗證：回傳的每個值都必須在原文逐字找得到，找不到就丟掉。
模型只能選取片段，不能編造內容。
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any, Dict, List, Optional

from . import llm
from .schema import FIELDS, describe_fields

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是履歷資料抽取器。使用者會給你一份已經填寫完成的履歷全文，
請把各欄位的值抽出來。

規則：
1. 只能從原文「逐字照抄」，不可改寫、翻譯、換算、補字或推論。抄不到就不要輸出那個鍵。
2. 表格以 | 分隔，同一列中標籤與值通常相鄰。
3. ␣ 代表沒填，不要當成值。
4. 只抽求職者本人填寫的資料。公司內部欄位（面談情形、任用與否、建議薪資、
   初試複試日期、主管簽名）一律略過。
5. 勾選題輸出被勾選的那一項（■ 或 ☑ 標記的），沒勾就不要輸出。
6. education、experience、family、reference 這類清單可能有多筆，
   依表格由上到下的順序輸出，不要輸出空白列。"""


VISION_RULE = """
7. 另附文件的頁面截圖：用截圖理解表格排版、判斷值屬於哪個欄位；
   但輸出的值一律以「履歷全文」的文字為準逐字照抄，不要抄截圖上看起來的字。"""


def read(text: str, host: str, model: str,
         images: Optional[List[bytes]] = None) -> Dict[str, Any]:
    """回傳 {欄位代碼: 值}；列表欄位回傳 {root: [{sub: 值}]}。

    images 給 PNG 頁面截圖時走視覺模式：模型同時看到排版與精確文字，
    合併儲存格、標籤與值的歸屬比攤平文字好判斷。逐字驗證照舊，
    看圖看錯的值會因為不在原文中而被丟掉。
    """
    user = f"可抽取的欄位：\n{describe_fields(include_special=False)}\n\n履歷全文：\n{text}"
    if images:
        content: List[Dict[str, Any]] = [{"type": "text", "text": user}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        data = llm.ask(host, SYSTEM_PROMPT + VISION_RULE, content, _schema(),
                       model=model, label=f"讀取履歷(視覺{len(images)}頁)")
    else:
        data = llm.ask(host, SYSTEM_PROMPT, user, _schema(), model=model, label="讀取履歷")
    return _keep_verbatim(data, text)


def _schema() -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    rows: Dict[str, Dict[str, Any]] = {}

    for f in FIELDS:
        if "[]." in f.key:
            root, sub = f.key.split("[].", 1)
            rows.setdefault(root, {})[sub] = {"type": "string"}
        else:
            props[f.key] = {"type": "string"}

    for root, sub_props in rows.items():
        props[root] = {"type": "array",
                       "items": {"type": "object", "properties": sub_props}}
    return {"type": "object", "properties": props}


def _squash(s: str) -> str:
    return re.sub(r"[\s　]+", "", s)


def _keep_verbatim(data: Dict[str, Any], source: str) -> Dict[str, Any]:
    haystack = _squash(source)
    kept: Dict[str, Any] = {}
    dropped: List[str] = []

    for key, value in data.items():
        if isinstance(value, list):
            rows = []
            for row in value:
                if not isinstance(row, dict):
                    continue
                clean = {k: v.strip() for k, v in row.items()
                         if isinstance(v, str) and v.strip() and _squash(v) in haystack}
                dropped += [f"{key}[].{k}" for k, v in row.items()
                            if isinstance(v, str) and v.strip() and k not in clean]
                if clean:
                    rows.append(clean)
            if rows:
                kept[key] = rows
        elif isinstance(value, str) and value.strip():
            if _squash(value) in haystack:
                kept[key] = value.strip()
            else:
                dropped.append(key)

    if dropped:
        # 只記欄位代碼——被丟掉的多半是模型改寫過的個資
        log.warning("捨棄非原文的抽取結果 欄位=%s", ",".join(sorted(set(dropped))))
    return kept

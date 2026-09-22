# -*- coding: utf-8 -*-
"""比對學校、公司這類名稱，以及學位的程度。

匯入履歷（這一筆是「我的資料」裡的哪一筆）與網頁填寫（這一筆是不是已經在平台上）
問的是同一件事，所以放在這裡共用。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

_NAME_NOISE_RE = re.compile(r"股份有限公司|有限公司|\(股\)|[\s,.。、・·()\-]")
# 學位只比程度：「學士」「大學」是同一級，「碩士」「研究所」也是
_DEGREE_LEVELS = (("博士", "phd", "doctor"), ("碩士", "研究所", "master", "mba"),
                  ("大學", "學士", "二技", "四技", "bachelor"), ("專科", "五專", "二專", "三專"),
                  ("高中", "高職", "high school"), ("國中",))


def name_key(text: str) -> str:
    """比對名稱用：全半形、大小寫、臺／台、公司後綴與標點都不算差別。"""
    text = unicodedata.normalize("NFKC", text or "").lower().replace("臺", "台")
    return _NAME_NOISE_RE.sub("", text)


def same_name(a: str, b: str, whole: bool = False) -> bool:
    """公司、學校的簡稱算同一個（「台灣大學」「國立臺灣大學」）；人名 whole 要整個一樣。"""
    x, y = name_key(a), name_key(b)
    if len(x) < 2 or len(y) < 2:
        return False
    return x == y if whole else (x in y or y in x)


def degree_level(text: str) -> Optional[int]:
    """0 博士、1 碩士、2 大學、3 專科、4 高中、5 國中；認不出來是 None。"""
    text = unicodedata.normalize("NFKC", text).lower()
    return next((i for i, words in enumerate(_DEGREE_LEVELS)
                 if any(w in text for w in words)), None)

"""使用者操作紀錄：日誌頁只顯示這個通道。

一般 log 是寫給開發者的（術語、座標、耗時），攤在頁面上使用者看不懂。
這裡的訊息要用白話講「做了什麼、結果如何」，和一般 log 相同的隱私規則：
永遠不要寫入 profile 的值。
"""
from __future__ import annotations

import logging

log = logging.getLogger("action")


def record(message: str, *args) -> None:
    """操作成功。"""
    log.info(message, *args)


def problem(message: str, *args) -> None:
    """操作失敗或有需要注意的結果。"""
    log.warning(message, *args)

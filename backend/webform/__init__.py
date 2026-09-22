# -*- coding: utf-8 -*-
"""網頁填寫：把「我的資料」填進求職平台的網頁（目前只有 Cake 個人檔案）。

跟本地 docx 填寫是兩個獨立的功能：入口、模組、文件都分開，共用的只有比對名稱（core.names）。
所有平台共用一個專用瀏覽器（一個視窗、一份登入設定檔），每個平台各自一個流程。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

from .. import config, db
from .browser import Browser
from .cake import CakeSite
from .session import Busy, Session

__all__ = ["Busy", "Session", "get_session", "shutdown"]

log = logging.getLogger(__name__)

SITES = {"cake": CakeSite}

_lock = threading.Lock()
_browser: Optional[Browser] = None
_sessions: Dict[str, Session] = {}


def _profile() -> dict:
    return db.get_kv("profile") or {}


def get_session(name: str) -> Session:
    """這個平台的流程。不認得的平台丟 KeyError。"""
    global _browser
    with _lock:
        if name not in _sessions:
            site = SITES[name]()
            if _browser is None:
                _browser = Browser(config.BROWSER_DIR)
            _sessions[name] = Session(site, _browser, _profile)
        return _sessions[name]


def shutdown() -> None:
    """程式關閉時把專用瀏覽器一起關掉。"""
    if _browser is not None and _browser.is_open:
        try:
            _browser.run(_browser.close(), timeout=10)
        except Exception:
            log.warning("關閉瀏覽器失敗", exc_info=True)

"""網頁填寫：把我的資料填進求職平台（目前只有 Cake）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import webform
from ..schemas import WebFormRunIn

router = APIRouter(prefix="/webform", tags=["webform"])


def _session(site: str) -> webform.Session:
    try:
        return webform.get_session(site)
    except KeyError:
        raise HTTPException(404, "不支援這個平台")


@router.get("/{site}")
def read_state(site: str) -> dict:
    """前端每秒輪詢：現在在哪一步、清單、進度、結果。"""
    return _session(site).snapshot()


@router.post("/{site}/open")
def open_browser(site: str) -> dict:
    """開專用瀏覽器、帶到平台的個人檔案頁；還沒登入就等使用者登入，登入後自動讀清單。"""
    s = _session(site)
    try:
        s.open()
    except webform.Busy as e:
        raise HTTPException(409, str(e))
    return s.snapshot()


@router.post("/{site}/refresh")
def refresh(site: str) -> dict:
    s = _session(site)
    try:
        s.refresh()
    except webform.Busy as e:
        raise HTTPException(409, str(e))
    return s.snapshot()


@router.post("/{site}/run")
def run(site: str, body: WebFormRunIn) -> dict:
    """照使用者確認過的清單一筆一筆存進平台。缺必填欄位的整批不開始。"""
    s = _session(site)
    try:
        s.run([p.model_dump() for p in body.items])
    except webform.Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return s.snapshot()


@router.post("/{site}/close")
def close_browser(site: str) -> dict:
    s = _session(site)
    s.close()
    return s.snapshot()

"""學過的格式：列出、忘掉。對映內容不外流，只回筆數與來源檔名。"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from .. import service
from ..schemas import FingerprintIn, LearnedFormatOut

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=List[LearnedFormatOut])
def list_formats() -> list:
    return service.learned_formats()


@router.post("/forget")
def forget(body: FingerprintIn) -> dict:
    """忘掉一份：下次上傳同一份表格會重新判讀（需要模型）。"""
    if not service.forget_format(body.fingerprint):
        raise HTTPException(404, "找不到這份學過的格式")
    return {"ok": True}

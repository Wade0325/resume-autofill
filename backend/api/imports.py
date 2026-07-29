"""匯入：從已填寫的履歷抽取資料寫回「我的資料」。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from .. import actions, config, service
from ..core.convert import ConversionError, doc_to_docx
from ..schemas import ImportApplyIn, ImportApplyOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("")
async def create_import(file: UploadFile = File(...)) -> dict:
    """收檔即回，讀取在背景跑；用 GET /imports/{id} 輪詢進度。"""
    name = file.filename or ""
    if not name.lower().endswith((".doc", ".docx")):
        raise HTTPException(400, "只接受 .doc 或 .docx 檔案")

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if not content:
        raise HTTPException(400, "檔案是空的")

    if name.lower().endswith(".doc"):
        try:
            content = doc_to_docx(content)
            log.info("轉檔 .doc → .docx %s", name)
        except ConversionError as e:
            log.warning("轉檔失敗 %s：%s", name, e)
            actions.problem("上傳履歷「%s」失敗：%s", name, e)
            raise HTTPException(400, str(e))

    return service.analyze_import(name, content)


@router.get("/{import_id}")
def read_import(import_id: str) -> dict:
    state = service.get_import(import_id)
    if state is None:
        raise HTTPException(404, "找不到這次匯入")
    return state


@router.get("/{import_id}/preview.pdf")
def preview_pdf(import_id: str) -> Response:
    """上傳履歷的排版預覽。LibreOffice 不在時回 503，前端只顯示欄位清單。"""
    try:
        pdf = service.render_import_pdf(import_id)
    except ConversionError as e:
        raise HTTPException(503, str(e))
    if pdf is None:
        raise HTTPException(404, "找不到這次匯入的檔案")
    return Response(content=pdf, media_type="application/pdf")


@router.post("/{import_id}/apply", response_model=ImportApplyOut)
def apply_import(import_id: str, body: ImportApplyIn) -> ImportApplyOut:
    applied = service.apply_import(import_id, body.row_ids)
    if applied is None:
        raise HTTPException(404, "找不到這次匯入")
    return ImportApplyOut(applied=applied)

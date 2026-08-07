"""匯入：從已填寫的履歷抽取資料寫回「我的資料」。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from .. import config, service
from ..schemas import ImportApplyIn, ImportApplyOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("")
async def create_import(file: UploadFile = File(...)) -> dict:
    """收檔即回，讀取在背景跑；用 GET /imports/{id} 輪詢進度。"""
    name = file.filename or ""
    if not name.lower().endswith((".pdf", ".docx")):
        raise HTTPException(400, "只接受 .pdf 或 .docx 檔案。舊版 .doc 請先用 Word 另存成 .docx")

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if not content:
        raise HTTPException(400, "檔案是空的")

    return service.analyze_import(name, content)


@router.get("/{import_id}")
def read_import(import_id: str) -> dict:
    state = service.get_import(import_id)
    if state is None:
        raise HTTPException(404, "找不到這次匯入")
    return state


@router.get("/{import_id}/source")
def source(import_id: str) -> Response:
    """上傳的原檔，交給前端自己渲染（PDF 用 pdf.js、docx 用 docx-preview）。"""
    content = service.import_source(import_id)
    if content is None:
        raise HTTPException(404, "找不到這次匯入的檔案")
    kind = "application/pdf" if service.input_path(import_id).suffix == ".pdf" else DOCX_MEDIA_TYPE
    return Response(content=content, media_type=kind)


@router.post("/{import_id}/apply", response_model=ImportApplyOut)
def apply_import(import_id: str, body: ImportApplyIn) -> ImportApplyOut:
    applied = service.apply_import(import_id, body.row_ids)
    if applied is None:
        raise HTTPException(404, "找不到這次匯入")
    return ImportApplyOut(applied=applied)

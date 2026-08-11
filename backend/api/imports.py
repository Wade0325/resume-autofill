"""匯入：從已填寫的履歷抽取資料寫回「我的資料」。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from .. import service
from ..schemas import ImportApplyIn, ImportApplyOut
from .uploads import DOCX_MEDIA_TYPE, read_upload

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("")
async def create_import(file: UploadFile = File(...)) -> dict:
    """收檔即回，讀取在背景跑；用 GET /imports/{id} 輪詢進度。"""
    name, content = await read_upload(file, (".pdf", ".docx"))
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

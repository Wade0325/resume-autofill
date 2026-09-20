"""匯入：從已填寫的履歷抽取資料寫回「我的資料」。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from .. import service
from ..schemas import ImportApplyIn, ImportApplyOut
from .uploads import DOCX_MEDIA_TYPE, WorkId, read_upload

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("")
async def create_import(file: UploadFile = File(...)) -> dict:
    """收檔即回，讀取在背景跑；用 GET /imports/{id} 輪詢進度。"""
    name, content = await read_upload(file, (".pdf", ".docx"))
    return service.analyze_import(name, content)


class TextIn(BaseModel):
    text: str


@router.post("/text")
async def create_from_text(body: TextIn) -> dict:
    """貼上的文字：手邊只有網頁版履歷、或從 PDF 複製出來的內容時用這個。"""
    if len(body.text.strip()) < 20:
        raise HTTPException(422, "貼上的內容太短，看不出是履歷")
    return service.analyze_import_text(body.text)


@router.get("/{import_id}")
def read_import(import_id: WorkId) -> dict:
    state = service.get_import(import_id)
    if state is None:
        raise HTTPException(404, "找不到這次匯入")
    return state


@router.get("/{import_id}/source")
def source(import_id: WorkId) -> Response:
    """上傳的原檔，交給前端自己渲染（PDF 用 pdf.js、docx 用 docx-preview）。"""
    content = service.import_source(import_id)
    if content is None:
        raise HTTPException(404, "找不到這次匯入的檔案")
    suffix = service.input_path(import_id).suffix
    kind = {".pdf": "application/pdf",
            ".txt": "text/plain; charset=utf-8"}.get(suffix, DOCX_MEDIA_TYPE)
    return Response(content=content, media_type=kind)


@router.post("/{import_id}/apply", response_model=ImportApplyOut)
def apply_import(import_id: WorkId, body: ImportApplyIn) -> ImportApplyOut:
    changed = service.apply_import(import_id, body.row_ids)
    if changed is None:
        raise HTTPException(404, "找不到這次匯入")
    return ImportApplyOut(applied=len(changed), changed=changed)

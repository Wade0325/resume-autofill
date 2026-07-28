"""匯入：從已填寫的履歷抽取資料寫回「我的資料」。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import actions, config, service
from ..core.convert import ConversionError, doc_to_docx
from ..core.llm import LlmUnavailable
from ..schemas import ImportApplyIn, ImportApplyOut, ImportPreviewOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", response_model=ImportPreviewOut)
async def create_import(file: UploadFile = File(...)) -> ImportPreviewOut:
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
            actions.problem("上傳「%s」失敗：%s", name, e)
            raise HTTPException(400, str(e))

    try:
        return service.analyze_import(name, content)
    except LlmUnavailable as e:
        log.warning("匯入失敗 %s：%s", name, e)
        actions.problem("上傳「%s」失敗：模型還沒啟動，無法讀取內容", name)
        raise HTTPException(503, f"模型未就緒：{e}")
    except Exception as e:
        log.exception("匯入失敗 %s：無法解析", name)
        actions.problem("上傳「%s」失敗：檔案無法解析", name)
        raise HTTPException(400, f"無法解析這份 docx：{e}")


@router.get("/{import_id}", response_model=ImportPreviewOut)
def read_import(import_id: str) -> ImportPreviewOut:
    preview = service.get_import(import_id)
    if preview is None:
        raise HTTPException(404, "找不到這次匯入")
    return preview


@router.post("/{import_id}/apply", response_model=ImportApplyOut)
def apply_import(import_id: str, body: ImportApplyIn) -> ImportApplyOut:
    applied = service.apply_import(import_id, body.row_ids)
    if applied is None:
        raise HTTPException(404, "找不到這次匯入")
    return ImportApplyOut(applied=applied)

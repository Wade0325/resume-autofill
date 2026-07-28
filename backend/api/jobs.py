"""主要流程：上傳 → 檢視計畫 → 修正 → 產生成果。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from .. import actions, config, db, service
from ..core.convert import ConversionError, doc_to_docx
from ..core.llm import LlmUnavailable
from ..schemas import MappingsIn, OutputOut, PlanOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=PlanOut)
async def create_job(file: UploadFile = File(...)) -> PlanOut:
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

    try:
        return service.analyze(name, content)
    except LlmUnavailable as e:
        # 沒有模型就沒有辦法判斷欄位，只有看過的格式能靠快取離線運作。
        # 原因要進 log，否則日誌上只剩一行 503 看不出發生什麼事
        log.warning("填寫失敗 %s：%s", name, e)
        actions.problem("上傳履歷「%s」失敗：模型還沒啟動", name)
        raise HTTPException(503, f"模型未就緒：{e}")
    except Exception as e:
        # 壞掉的 docx 是使用者輸入問題，不該回 500
        log.exception("填寫失敗 %s：無法解析", name)
        actions.problem("上傳履歷「%s」失敗：檔案無法解析", name)
        raise HTTPException(400, f"無法解析這份 docx：{e}")


@router.get("/{job_id}", response_model=PlanOut)
def read_job(job_id: str) -> PlanOut:
    plan = service.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.get("/{job_id}/preview.pdf")
def preview_pdf(job_id: str, which: str = "original") -> Response:
    """排版預覽。LibreOffice 不在時回 503，前端退回結構化對照。"""
    if which not in ("original", "filled"):
        raise HTTPException(422, "which 必須是 original 或 filled")
    try:
        pdf = service.render_preview_pdf(job_id, which)
    except ConversionError as e:
        raise HTTPException(503, str(e))
    if pdf is None:
        raise HTTPException(404, "找不到這個 job")
    return Response(content=pdf, media_type="application/pdf")


@router.get("/{job_id}/preview")
def read_preview(job_id: str) -> dict:
    result = service.get_preview(job_id)
    if result is None:
        raise HTTPException(404, "找不到這個 job")
    return result


@router.patch("/{job_id}/mappings", response_model=PlanOut)
def fix_mappings(job_id: str, body: MappingsIn) -> PlanOut:
    try:
        plan = service.apply_fixes(job_id, [(f.slot_id, f.field_key) for f in body.fixes])
    except ValueError as e:
        raise HTTPException(422, str(e))
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.post("/{job_id}/output", response_model=OutputOut)
def make_output(job_id: str) -> OutputOut:
    result = service.write_output(job_id)
    if result is None:
        raise HTTPException(404, "找不到這個 job")
    return OutputOut(**result)


@router.get("/{job_id}/output")
def download_output(job_id: str) -> FileResponse:
    path = service.output_path(job_id)
    if not path.exists():
        raise HTTPException(404, "尚未產生成果檔，請先呼叫 POST /output")
    job = db.get_job(job_id)
    stem = (job["filename"].rsplit(".", 1)[0] if job else "resume")
    actions.record("下載履歷「%s_已填寫.docx」成功", stem)
    return FileResponse(
        path, filename=f"{stem}_已填寫.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.delete("/{job_id}")
def remove_job(job_id: str) -> dict:
    if not service.delete(job_id):
        raise HTTPException(404, "找不到這個 job")
    return {"ok": True}

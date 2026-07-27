"""主要流程：上傳 → 檢視計畫 → 修正 → 產生成果。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, db, service
from ..schemas import MappingsIn, OutputOut, PlanOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=PlanOut)
async def create_job(file: UploadFile = File(...)) -> PlanOut:
    name = file.filename or ""
    if name.lower().endswith(".doc"):
        raise HTTPException(400, "舊版 .doc 不支援，請先用 Word 或 LibreOffice 轉成 .docx")
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "只接受 .docx 檔案")

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if not content:
        raise HTTPException(400, "檔案是空的")

    try:
        return service.analyze(name, content)
    except Exception as e:
        # 壞掉的 docx 是使用者輸入問題，不該回 500
        log.exception("解析失敗 filename=%s", name)
        raise HTTPException(400, f"無法解析這份 docx：{e}")


@router.get("/{job_id}", response_model=PlanOut)
def read_job(job_id: str) -> PlanOut:
    plan = service.get_plan(job_id)
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.patch("/{job_id}/mappings", response_model=PlanOut)
def fix_mappings(job_id: str, body: MappingsIn) -> PlanOut:
    try:
        plan = service.apply_fixes(job_id, [(f.anchor_id, f.field_key) for f in body.fixes])
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
    return FileResponse(
        path, filename=f"{stem}_已填寫.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.delete("/{job_id}")
def remove_job(job_id: str) -> dict:
    if not service.delete(job_id):
        raise HTTPException(404, "找不到這個 job")
    return {"ok": True}

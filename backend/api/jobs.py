"""主要流程：上傳 → 檢視計畫 → 修正 → 產生成果。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from .. import actions, config, db, service
from ..schemas import MappingsIn, OutputOut, PlanOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("")
async def create_job(file: UploadFile = File(...)) -> dict:
    """收檔即回，分析在背景跑；用 GET /jobs/{id} 輪詢進度。"""
    name = file.filename or ""
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "只接受 .docx 檔案。舊版 .doc 請先用 Word 另存成 .docx")

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if not content:
        raise HTTPException(400, "檔案是空的")

    job_id = service.analyze(name, content)
    return {"job_id": job_id, "status": "processing", "filename": name}


@router.get("/{job_id}")
def read_job(job_id: str) -> dict:
    state = service.get_job_state(job_id)
    if state is None:
        raise HTTPException(404, "找不到這個 job")
    return state


def _ensure_ready(job_id: str) -> None:
    """還在分析或已失敗的 job，其餘操作一律擋下。"""
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "找不到這個 job")
    if job["status"] == "processing":
        raise HTTPException(409, "還在分析中，請稍候")
    if job["status"] == "failed":
        raise HTTPException(409, job.get("error") or "這次分析失敗了，請重新上傳")


@router.get("/{job_id}/preview.docx")
def preview_docx(job_id: str, which: str = "original") -> Response:
    """左右對照用的原稿與填寫後文件，前端自己渲染。"""
    if which not in ("original", "filled"):
        raise HTTPException(422, "which 必須是 original 或 filled")
    _ensure_ready(job_id)
    content = service.preview_docx(job_id, which)
    if content is None:
        raise HTTPException(404, "找不到這個 job")
    return Response(content=content, media_type=DOCX_MEDIA_TYPE)


@router.patch("/{job_id}/mappings", response_model=PlanOut)
def fix_mappings(job_id: str, body: MappingsIn) -> PlanOut:
    _ensure_ready(job_id)
    try:
        plan = service.apply_fixes(job_id, [(f.slot_id, f.field_key) for f in body.fixes])
    except ValueError as e:
        raise HTTPException(422, str(e))
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.post("/{job_id}/output", response_model=OutputOut)
def make_output(job_id: str) -> OutputOut:
    _ensure_ready(job_id)
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
        media_type=DOCX_MEDIA_TYPE)

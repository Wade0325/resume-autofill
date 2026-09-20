"""主要流程：上傳 → 檢視計畫 → 修正 → 產生成果。"""
from __future__ import annotations

import re
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from .. import actions, db, service
from ..schemas import (
    ApplyIn,
    BatchIdsIn,
    BatchOutputOut,
    BatchStatusOut,
    JobHistoryOut,
    MappingsIn,
    OutputOut,
    PlanOut,
    TypedIn,
)
from .uploads import DOCX_MEDIA_TYPE, WorkId, read_upload

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("")
async def create_job(file: UploadFile = File(...)) -> dict:
    """收檔即回，分析在背景跑；用 GET /jobs/{id} 輪詢進度。"""
    name, content = await read_upload(file, (".docx",))
    job_id = service.analyze(name, content)
    return {"job_id": job_id, "status": "processing", "filename": name}


# 批次那幾支要排在 /{job_id} 前面：路由照宣告順序比對，擺後面的話
# /jobs/batch.zip 會先被當成 job_id 攔下來
_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _batch_ids(raw: List[str]) -> List[str]:
    """批次的工作代碼。代碼會接進檔案路徑，格式不對一律不認（同 WorkId 的規則）。"""
    ids = [x for x in raw if x]
    if not ids:
        raise HTTPException(422, "沒有指定任何工作")
    if len(ids) > service.BATCH_MAX:
        raise HTTPException(422, f"一次最多 {service.BATCH_MAX} 份")
    bad = [x for x in ids if not _ID_RE.fullmatch(x)]
    if bad:
        raise HTTPException(422, "不認得的工作代碼")
    return ids


@router.get("/batch", response_model=List[BatchStatusOut])
def batch_status(ids: str = Query(..., description="工作代碼，逗號分隔")) -> list:
    """一次問整批的進度。畫面每兩秒輪詢這一支，所以不回計畫內容。"""
    return service.batch_status(_batch_ids(ids.split(",")))


@router.post("/batch/output", response_model=List[BatchOutputOut])
def batch_output(body: BatchIdsIn) -> list:
    """整批套用。一份失敗不影響其他份，逐份回報結果。"""
    return service.batch_output(_batch_ids(body.job_ids))


@router.get("/batch.zip")
def batch_zip(ids: str = Query(..., description="工作代碼，逗號分隔")) -> Response:
    """把已經產生的成品打包下載。還沒產生的那幾份直接略過。"""
    content, count = service.batch_zip(_batch_ids(ids.split(",")))
    if not count:
        raise HTTPException(404, "這一批還沒有任何成果檔，請先套用")
    # 中文檔名要用 RFC 5987 那一式；同時留一個 ASCII 的備援，舊瀏覽器才不會拿到亂碼
    name = f"已填寫履歷_{count}份.zip"
    return Response(
        content=content, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="resumes_{count}.zip"; '
                 f"filename*=UTF-8''{quote(name)}"})


@router.get("/{job_id}")
def read_job(job_id: WorkId) -> dict:
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
def preview_docx(job_id: WorkId, which: str = "original", highlight: bool = True) -> Response:
    """左右對照用的原稿與填寫後文件，前端自己渲染。

    highlight=false 的 filled 就是下載成品的內容（同一條寫入路徑，只差沒標黃底）——
    列印／存成 PDF 走這個，印出來交出去的東西不會帶著黃底。
    """
    if which not in ("original", "filled"):
        raise HTTPException(422, "which 必須是 original 或 filled")
    _ensure_ready(job_id)
    content = service.preview_docx(job_id, which, highlight=highlight)
    if content is None:
        raise HTTPException(404, "找不到這個 job")
    return Response(content=content, media_type=DOCX_MEDIA_TYPE)


@router.patch("/{job_id}/mappings", response_model=PlanOut)
def fix_mappings(job_id: WorkId, body: MappingsIn) -> PlanOut:
    _ensure_ready(job_id)
    try:
        plan = service.apply_fixes(
            job_id, [(f.slot_id, f.field_key, f.ordinal) for f in body.fixes])
    except ValueError as e:
        raise HTTPException(422, str(e))
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.get("", response_model=List[JobHistoryOut])
def list_jobs() -> list:
    """填寫紀錄：最近填過的表單，保留期內可以重新下載。"""
    return service.recent_jobs()


@router.post("/{job_id}/cancel")
def cancel(job_id: WorkId) -> dict:
    """取消分析。正在問模型的那一批跑完才會停。"""
    if not service.cancel(job_id):
        raise HTTPException(404, "這份不在分析中")
    return {"ok": True}


@router.post("/{job_id}/reanalyze")
def reanalyze(job_id: WorkId) -> dict:
    """重新判讀這一份，不用學過的格式（學過的對映填錯時用）。需要模型。"""
    if not service.reanalyze(job_id):
        raise HTTPException(404, "找不到這個 job，或上傳的原檔已經過期清掉了")
    return {"ok": True}


@router.patch("/{job_id}/value", response_model=PlanOut)
def set_value(job_id: WorkId, body: TypedIn) -> PlanOut:
    """把某一格改成自己打的字（空字串＝改回自動判斷的值）。預覽立刻跟著變。"""
    plan = service.set_typed(job_id, body.slot_id, body.value)
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.patch("/{job_id}/apply", response_model=PlanOut)
def set_apply(job_id: WorkId, body: ApplyIn) -> PlanOut:
    """「這次應徵」：填完立刻重算計畫與預覽，不必重新分析。"""
    plan = service.set_apply(job_id, body.values)
    if plan is None:
        raise HTTPException(404, "找不到這個 job")
    return plan


@router.post("/{job_id}/output", response_model=OutputOut)
def make_output(job_id: WorkId) -> OutputOut:
    _ensure_ready(job_id)
    result = service.write_output(job_id)
    if result is None:
        raise HTTPException(404, "找不到這個 job")
    return OutputOut(**result)


@router.get("/{job_id}/output")
def download_output(job_id: WorkId) -> FileResponse:
    path = service.output_path(job_id)
    if not path.exists():
        raise HTTPException(404, "尚未產生成果檔，請先呼叫 POST /output")
    job = db.get_job(job_id)
    stem = (job["filename"].rsplit(".", 1)[0] if job else "resume")
    actions.record("下載履歷「%s_已填寫.docx」成功", stem)
    return FileResponse(
        path, filename=f"{stem}_已填寫.docx",
        media_type=DOCX_MEDIA_TYPE)

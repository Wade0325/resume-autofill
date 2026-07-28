"""流程編排：填寫（我的資料 → 空白履歷）與匯入（已填履歷 → 我的資料）。

API 層只管 HTTP，core 只管演算法，順序寫在這裡。
"""
from __future__ import annotations

import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import actions, config, db
from .core import convert, document, llm, planner, preview, reader, writer
from .core.document import Slot
from .core.schema import BY_KEY
from .schemas import (ImportPreviewOut, ImportRow, PlanItem, PlanOut, PlanStats)

log = logging.getLogger(__name__)


def job_dir(job_id: str) -> Path:
    return config.JOBS_DIR / job_id


def input_path(job_id: str) -> Path:
    return job_dir(job_id) / "input.docx"


def output_path(job_id: str) -> Path:
    return job_dir(job_id) / "output.docx"


def _values_of(extracted: Dict[str, Any]) -> set:
    """把 reader 的輸出攤平成一組值，用來認出哪些格子裝的是使用者資料。"""
    out = set()
    for value in extracted.values():
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    out.update(str(v) for v in row.values() if str(v).strip())
        elif str(value).strip():
            out.add(str(value))
    return out


def _save_upload(job_id: str, content: bytes) -> Path:
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    path = input_path(job_id)
    path.write_bytes(content)
    return path


# --------------------------------------------------------------------------
# 填寫
# --------------------------------------------------------------------------
def analyze(filename: str, content: bytes) -> PlanOut:
    job_id = uuid.uuid4().hex[:12]
    src = _save_upload(job_id, content)
    log.info("上傳 %s (%.1f KB) job=%s", filename, len(content) / 1024, job_id)

    t0 = time.perf_counter()
    # 先讀出這份文件已經有的值，才分得出哪些非空格子是使用者資料（可覆蓋）、
    # 哪些是表格印好的欄位名稱（不能碰）
    existing = reader.read(document.text_only(str(src)), config.LLM_HOST, config.LLM_MODEL)
    text, slots = document.load(str(src), _values_of(existing))
    fp = document.fingerprint(slots)
    cached = db.get_template(fp)
    log.info("解析完成 位置=%d 可覆蓋=%d 全文=%d字 fingerprint=%s 範本快取=%s",
             len(slots), sum(1 for s in slots if s.existing.strip()), len(text), fp,
             "命中" if cached else "未命中")

    decisions = planner.decide(text, slots, config.LLM_HOST, config.LLM_MODEL, cached)

    # 第二輪修正：只在有模型新判的格子時跑（純快取代表使用者確認過）。
    # 先做零成本的確定性對齊（標籤↔欄位、白名單外格子、期間欄拆併），
    # 再由模型指認學經歷每一列對應清單第幾筆（分級列會錯位的根源）。
    if any(d[3] == "model" for d in decisions.values()):
        headers = document.slot_headers(str(src), slots)
        profile = db.get_kv("profile") or {}
        decisions = planner.align_labels(slots, decisions, headers)
        decisions = planner.assign_rows(slots, decisions, profile, headers,
                                        config.LLM_HOST, config.LLM_MODEL)
        if config.LLM_VERIFY:
            decisions = planner.verify(slots, decisions, profile, headers,
                                       config.LLM_HOST, config.LLM_MODEL)

    db.create_job(job_id, filename, fp, [s.to_dict() for s in slots],
                  {k: list(v) for k, v in decisions.items()})

    plan = _render(job_id, filename, fp, bool(cached), slots, decisions)
    log.info("比對完成 fill=%d skip=%d by_source=%s 耗時=%dms",
             plan.stats.fill, plan.stats.skip, plan.stats.by_source,
             int((time.perf_counter() - t0) * 1000))
    actions.record("上傳履歷「%s」成功", filename)
    return plan


def get_plan(job_id: str) -> Optional[PlanOut]:
    job = db.get_job(job_id)
    if not job:
        return None
    slots = [Slot(**s) for s in job["anchors"]]
    decisions = {k: tuple(v) for k, v in job["decided"].items()}
    return _render(job_id, job["filename"], job["fingerprint"],
                   bool(db.get_template(job["fingerprint"])), slots, decisions)


def get_preview(job_id: str) -> Optional[Dict[str, Any]]:
    """把原始文件攤成結構化區塊，前端拿 plan 的值疊上去做左右對照。"""
    job = db.get_job(job_id)
    if not job:
        return None
    slot_ids = {s["id"] for s in job["anchors"]}
    return {"job_id": job_id,
            "blocks": preview.build(str(input_path(job_id)), slot_ids)}


def render_preview_pdf(job_id: str, which: str) -> Optional[bytes]:
    """排版預覽的 PDF。original＝原始文件、filled＝套用我的資料後。

    original 轉一次就快取在 job 目錄；filled 每次重算——使用者剛改過
    對映就要看到新結果，LibreOffice 轉一次約一兩秒，可以接受。
    """
    job = db.get_job(job_id)
    if not job:
        return None

    if which == "original":
        cache = job_dir(job_id) / "original.pdf"
        if not cache.exists():
            cache.write_bytes(convert.docx_to_pdf(input_path(job_id).read_bytes()))
        return cache.read_bytes()

    slots = [Slot(**s) for s in job["anchors"]]
    decisions = {k: tuple(v) for k, v in job["decided"].items()}
    settings = db.get_settings()
    ops, _ = planner.build_plan(
        slots, db.get_kv("profile") or {}, decisions,
        min_confidence=settings["min_confidence"],
        allow_sensitive=settings["allow_sensitive"])
    with tempfile.TemporaryDirectory(prefix="preview_") as tmp:
        filled = Path(tmp) / "filled.docx"
        # 預覽一律標黃底，才看得出資料落在哪一格；下載的成品仍依設定
        writer.apply_ops(str(input_path(job_id)), str(filled), ops, highlight=True)
        return convert.docx_to_pdf(filled.read_bytes())


def apply_fixes(job_id: str, fixes: List[Tuple[str, str]]) -> Optional[PlanOut]:
    """套用使用者修正。只改決策再重算，不會再呼叫模型。"""
    job = db.get_job(job_id)
    if not job:
        return None
    slots = [Slot(**s) for s in job["anchors"]]
    decisions = {k: tuple(v) for k, v in job["decided"].items()}
    valid = {s.id for s in slots}

    for slot_id, field_key in fixes:
        if slot_id not in valid:
            raise ValueError(f"位置不存在：{slot_id}")
        if field_key not in BY_KEY and field_key not in ("__SKIP__", "__UNKNOWN__"):
            raise ValueError(f"未知欄位代碼：{field_key}")
        previous = decisions.get(slot_id)
        old = previous[0] if previous else ""
        label = previous[4] if previous else ""
        decisions[slot_id] = (field_key, previous[1] if previous else 0, 1.0,
                              "manual", label)
        # 這是日後改進提示詞的唯一依據
        log.info("使用者修正 %s：%s → %s", slot_id, old or "(未決定)", field_key)
        actions.record("修改欄位「%s」", label or slot_id)

    db.update_job(job_id, decided={k: list(v) for k, v in decisions.items()})
    return _render(job_id, job["filename"], job["fingerprint"],
                   bool(db.get_template(job["fingerprint"])), slots, decisions)


def write_output(job_id: str) -> Optional[Dict[str, Any]]:
    job = db.get_job(job_id)
    if not job:
        return None
    slots = [Slot(**s) for s in job["anchors"]]
    decisions = {k: tuple(v) for k, v in job["decided"].items()}
    settings = db.get_settings()

    ops, _ = planner.build_plan(
        slots, db.get_kv("profile") or {}, decisions,
        min_confidence=settings["min_confidence"],
        allow_sensitive=settings["allow_sensitive"])

    t0 = time.perf_counter()
    result = writer.apply_ops(str(input_path(job_id)), str(output_path(job_id)),
                              ops, highlight=settings["highlight_filled"])
    log.info("寫檔完成 written=%d failed=%d 耗時=%dms",
             result["written"], result["failed"], int((time.perf_counter() - t0) * 1000))
    for f in result["fail"]:
        log.warning("寫入失敗 slot=%s error=%s", f.get("slot"), f.get("error"))
    if result["failed"]:
        actions.problem("匯出履歷「%s」不完整：有 %d 格沒填上", job["filename"], result["failed"])
    else:
        actions.record("匯出履歷「%s」成功", job["filename"])

    # 記住這次的決策，同一份表格下次完全不必問模型。
    # 連略過的位置也要記，否則下次還會為了那些格子再呼叫一次。
    mapping = dict(db.get_template(job["fingerprint"]))
    mapping.update({sid: {"field_key": key, "ordinal": ordinal, "label": label}
                    for sid, (key, ordinal, _conf, _src, label) in decisions.items()})
    db.put_template(job["fingerprint"], mapping, source_name=job["filename"])
    db.update_job(job_id, status="written")
    log.info("範本已學習 fingerprint=%s 位置=%d", job["fingerprint"], len(mapping))

    return {"job_id": job_id, "written": result["written"],
            "failed": result["failed"], "learned": len(mapping),
            "download_url": f"/api/jobs/{job_id}/output"}


def delete(job_id: str) -> bool:
    job = db.get_job(job_id)
    if not job:
        return False
    db.delete_job(job_id)
    db._rmtree(job_dir(job_id))
    log.info("已刪除 job=%s", job_id)
    actions.record("刪除履歷「%s」", job["filename"])
    return True


def _render(job_id: str, filename: str, fingerprint: str, cached: bool,
            slots: List[Slot], decisions: Dict[str, Any]) -> PlanOut:
    settings = db.get_settings()
    ops, skipped = planner.build_plan(
        slots, db.get_kv("profile") or {}, decisions,
        min_confidence=settings["min_confidence"],
        allow_sensitive=settings["allow_sensitive"])

    items = [_item(o, "fill") for o in ops] + [_item(s, "skip") for s in skipped]
    items.sort(key=lambda i: i.slot_id)

    by_source: Dict[str, int] = {}
    for o in ops:
        by_source[o.source] = by_source.get(o.source, 0) + 1

    return PlanOut(
        job_id=job_id, filename=filename, fingerprint=fingerprint,
        template_cached=cached, llm_available=llm.available(config.LLM_HOST),
        stats=PlanStats(slots=len(slots), fill=len(ops), skip=len(skipped),
                        by_source=by_source),
        items=items)


def _item(op, status: str) -> PlanItem:
    return PlanItem(
        slot_id=op.slot.id, label=op.label, kind=op.slot.kind,
        options=op.slot.options,
        field_key=op.field_key, ordinal=op.ordinal, value=str(op.value),
        existing=op.slot.existing, confidence=op.confidence, source=op.source,
        status=status, note=op.note)


# --------------------------------------------------------------------------
# 匯入
# --------------------------------------------------------------------------
def analyze_import(filename: str, content: bytes) -> ImportPreviewOut:
    import_id = uuid.uuid4().hex[:12]
    src = _save_upload(import_id, content)
    log.info("匯入上傳 %s (%.1f KB) import=%s", filename, len(content) / 1024, import_id)

    t0 = time.perf_counter()
    text = document.text_only(str(src))
    extracted = reader.read(text, config.LLM_HOST, config.LLM_MODEL)
    db.create_import(import_id, filename, extracted)

    rows = _import_rows(extracted)
    log.info("匯入讀取完成 全文=%d字 欄位=%d 需覆蓋=%d 耗時=%dms",
             len(text), len(rows), sum(1 for r in rows if not r.default_checked),
             int((time.perf_counter() - t0) * 1000))
    actions.record("上傳履歷「%s」成功，等待確認匯入", filename)
    return ImportPreviewOut(import_id=import_id, filename=filename, rows=rows)


def get_import(import_id: str) -> Optional[ImportPreviewOut]:
    record = db.get_import(import_id)
    if not record:
        return None
    return ImportPreviewOut(import_id=import_id, filename=record["filename"],
                            rows=_import_rows(record["extracted"]))


def apply_import(import_id: str, row_ids: List[str]) -> Optional[int]:
    record = db.get_import(import_id)
    if not record:
        return None
    rows = {r.row_id: r for r in _import_rows(record["extracted"])}

    profile = db.get_kv("profile") or {}
    applied = []
    for row_id in row_ids:
        row = rows.get(row_id)
        if row is None:
            continue
        planner.set_value(profile, row.field_key, row.incoming, row.ordinal)
        applied.append(row.field_key)
    db.put_kv("profile", profile)

    # 只記欄位代碼——incoming 全是個資
    log.info("匯入寫入 選取=%d 欄位=%s", len(applied), ",".join(sorted(set(applied))))
    actions.record("匯入履歷「%s」成功", record["filename"])
    return len(applied)


def _import_rows(extracted: Dict[str, Any]) -> List[ImportRow]:
    profile = db.get_kv("profile") or {}
    rows: List[ImportRow] = []

    def add(field_key: str, ordinal: int, value: Any) -> None:
        if field_key not in BY_KEY or not str(value).strip():
            return
        current = str(planner.get_value(profile, field_key, ordinal) or "")
        rows.append(ImportRow(
            row_id=f"{field_key}#{ordinal}", field_key=field_key, ordinal=ordinal,
            current=current, incoming=str(value).strip(), default_checked=not current))

    for key, value in extracted.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    for sub, sub_value in item.items():
                        add(f"{key}[].{sub}", index, sub_value)
        else:
            add(key, 0, value)

    rows.sort(key=lambda r: (r.field_key, r.ordinal))
    return rows

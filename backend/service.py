"""核心流程編排：extract → decide → build_plan → apply_ops。

這是唯一知道處理順序的地方；API 層只管 HTTP，core/ 只管演算法。
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config, db
from .core import extractor, matcher, writer
from .core.llm import get_backend
from .core.schema import BY_KEY
from .schemas import (ImportPreviewOut, ImportRow, PlanItem, PlanOut, PlanStats)

log = logging.getLogger(__name__)


def job_dir(job_id: str) -> Path:
    return config.JOBS_DIR / job_id


def input_path(job_id: str) -> Path:
    return job_dir(job_id) / "input.docx"


def output_path(job_id: str) -> Path:
    return job_dir(job_id) / "output.docx"


def _backend():
    return get_backend({"backend": "llamacpp", "host": config.LLM_HOST,
                        "model": config.LLM_MODEL})


def analyze(filename: str, content: bytes) -> PlanOut:
    """收下上傳的 docx，解析並產生填寫計畫。"""
    job_id = uuid.uuid4().hex[:12]
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    src = input_path(job_id)
    src.write_bytes(content)
    log.info("上傳 %s (%.1f KB) job=%s", filename, len(content) / 1024, job_id)

    t0 = time.perf_counter()
    # 一律連已填寫的位置一起抽：使用者上傳的可能是填過的舊履歷，
    # 那些格子要能被「我的資料」覆蓋掉，沒抽出來就無從覆蓋。
    # 對空白表格而言這是嚴格超集，不影響原本的結果。
    data = extractor.extract(str(src), include_filled=True)
    anchors, fingerprint = data["anchors"], data["fingerprint"]
    log.info("解析完成 anchors=%d fingerprint=%s 耗時=%dms",
             len(anchors), fingerprint, int((time.perf_counter() - t0) * 1000))

    cached = db.get_template(fingerprint)
    log.info("範本快取%s fingerprint=%s entries=%d",
             "命中" if cached else "未命中", fingerprint, len(cached))

    backend = _backend()
    t1 = time.perf_counter()
    decided = matcher.decide(anchors, backend, cached_map=cached)
    db.create_job(job_id, filename, fingerprint, anchors,
                  {k: list(v) for k, v in decided.items()})

    plan = _render(job_id, filename, fingerprint, bool(cached),
                   backend.name != "null", anchors, decided)
    log.info("比對完成 fill=%d skip=%d by_source=%s 耗時=%dms",
             plan.stats.fill, plan.stats.skip, plan.stats.by_source,
             int((time.perf_counter() - t1) * 1000))
    return plan


def get_plan(job_id: str) -> Optional[PlanOut]:
    job = db.get_job(job_id)
    if not job:
        return None
    decided = {k: tuple(v) for k, v in job["decided"].items()}
    return _render(job_id, job["filename"], job["fingerprint"],
                   bool(db.get_template(job["fingerprint"])), True,
                   job["anchors"], decided)


def apply_fixes(job_id: str, fixes: List[Tuple[str, str]]) -> Optional[PlanOut]:
    """套用使用者修正。只改 decided 再重算，完全不呼叫模型。"""
    job = db.get_job(job_id)
    if not job:
        return None
    decided = {k: tuple(v) for k, v in job["decided"].items()}
    valid_ids = {a["id"] for a in job["anchors"]}

    for anchor_id, field_key in fixes:
        if anchor_id not in valid_ids:
            raise ValueError(f"anchor_id 不存在：{anchor_id}")
        if field_key not in BY_KEY and field_key not in ("__SKIP__", "__UNKNOWN__"):
            raise ValueError(f"未知欄位代碼：{field_key}")
        old = decided.get(anchor_id, ("", 0.0, ""))[0]
        decided[anchor_id] = (field_key, 1.0, "manual")
        # 這條是日後補強規則別名表的唯一依據，別降級成 debug
        log.info("使用者修正 %s：%s → %s", anchor_id, old or "(未決定)", field_key)

    db.update_job(job_id, decided={k: list(v) for k, v in decided.items()})
    return _render(job_id, job["filename"], job["fingerprint"],
                   bool(db.get_template(job["fingerprint"])), True,
                   job["anchors"], decided)


def write_output(job_id: str) -> Optional[Dict[str, Any]]:
    """產生填好的 docx，並把這次的對映存成範本快取。"""
    job = db.get_job(job_id)
    if not job:
        return None
    profile = db.get_kv("profile") or {}
    settings = db.get_settings()
    decided = {k: tuple(v) for k, v in job["decided"].items()}

    ops, _ = matcher.build_plan(
        job["anchors"], profile, decided,
        min_confidence=settings["min_confidence"],
        allow_sensitive=settings["allow_sensitive"])

    t0 = time.perf_counter()
    result = writer.apply_ops(str(input_path(job_id)), str(output_path(job_id)),
                              ops, highlight=settings["highlight_filled"])
    log.info("寫檔完成 written=%d failed=%d 耗時=%dms",
             result["written"], result["failed"], int((time.perf_counter() - t0) * 1000))
    for f in result["fail"]:
        log.warning("寫入失敗 anchor=%s error=%s", f.get("anchor"), f.get("error"))

    # 學習：把這次的決策記住，下次同一份表格直接命中快取，0 次模型呼叫
    mapping = dict(db.get_template(job["fingerprint"]))
    mapping.update({o.anchor["id"]: o.field_key for o in ops})
    db.put_template(job["fingerprint"], mapping, source_name=job["filename"])
    db.update_job(job_id, status="written")
    log.info("範本已學習 fingerprint=%s entries=%d", job["fingerprint"], len(mapping))

    return {"job_id": job_id, "written": result["written"],
            "failed": result["failed"], "learned": len(mapping),
            "download_url": f"/api/jobs/{job_id}/output"}


def delete(job_id: str) -> bool:
    if not db.get_job(job_id):
        return False
    db.delete_job(job_id)
    db._rmtree(job_dir(job_id))
    log.info("已刪除 job=%s", job_id)
    return True


def _render(job_id: str, filename: str, fingerprint: str, cached: bool,
            llm_available: bool, anchors: List[Dict[str, Any]],
            decided: Dict[str, Any]) -> PlanOut:
    """把 decided 轉成前端要的單一 items 列表。"""
    profile = db.get_kv("profile") or {}
    settings = db.get_settings()
    ops, skipped = matcher.build_plan(
        anchors, profile, decided,
        min_confidence=settings["min_confidence"],
        allow_sensitive=settings["allow_sensitive"])

    items = [_item(o, "fill") for o in ops] + [_item(s, "skip") for s in skipped]
    items.sort(key=lambda i: i.anchor_id)

    by_source: Dict[str, int] = {}
    for o in ops:
        by_source[o.source] = by_source.get(o.source, 0) + 1

    return PlanOut(
        job_id=job_id, filename=filename, fingerprint=fingerprint,
        template_cached=cached, llm_available=llm_available,
        stats=PlanStats(anchors=len(anchors), fill=len(ops),
                        skip=len(skipped), by_source=by_source),
        items=items)


def _item(op, status: str) -> PlanItem:
    return PlanItem(
        anchor_id=op.anchor["id"], label=op.anchor["label"],
        kind=op.anchor["kind"], options=op.anchor.get("options", []),
        field_key=op.field_key, value=str(op.value),
        existing=op.anchor.get("existing", ""), confidence=op.confidence,
        source=op.source, status=status, note=op.note)


# --------------------------------------------------------------------------
# 匯入：已填寫的履歷 → 我的資料
# --------------------------------------------------------------------------
def analyze_import(filename: str, content: bytes) -> ImportPreviewOut:
    import_id = uuid.uuid4().hex[:12]
    d = job_dir(import_id)
    d.mkdir(parents=True, exist_ok=True)
    src = input_path(import_id)
    src.write_bytes(content)
    log.info("匯入上傳 %s (%.1f KB) import=%s", filename, len(content) / 1024, import_id)

    t0 = time.perf_counter()
    data = extractor.extract(str(src), include_filled=True)
    anchors, fingerprint = data["anchors"], data["fingerprint"]

    decided = matcher.decide(anchors, _backend(),
                             cached_map=db.get_template(fingerprint))
    db.create_job(import_id, filename, fingerprint, anchors,
                  {k: list(v) for k, v in decided.items()})

    rows = _import_rows(anchors, decided)
    log.info("匯入解析完成 anchors=%d 可匯入=%d 需覆蓋=%d 耗時=%dms",
             len(anchors), len(rows), sum(1 for r in rows if not r.default_checked),
             int((time.perf_counter() - t0) * 1000))
    return ImportPreviewOut(import_id=import_id, filename=filename, rows=rows)


def apply_import(import_id: str, anchor_ids: List[str]) -> Optional[int]:
    job = db.get_job(import_id)
    if not job:
        return None
    decided = {k: tuple(v) for k, v in job["decided"].items()}
    rows = {r.anchor_id: r for r in _import_rows(job["anchors"], decided)}

    profile = db.get_kv("profile") or {}
    applied = []
    for aid in anchor_ids:
        row = rows.get(aid)
        if row is None:
            continue
        matcher.set_value(profile, row.field_key, row.incoming, row.ordinal)
        applied.append(row.field_key)
    db.put_kv("profile", profile)

    # 只記欄位代碼不記值——incoming 全是個資
    log.info("匯入寫入 選取=%d 欄位=%s", len(applied), ",".join(sorted(set(applied))))
    return len(applied)


def _import_rows(anchors: List[Dict[str, Any]],
                 decided: Dict[str, Any]) -> List[ImportRow]:
    """把「anchor 的既有內容」轉成可匯入的欄位列表。"""
    profile = db.get_kv("profile") or {}
    ordinals = matcher.assign_ordinals(anchors, decided)
    by_id = {a["id"]: a for a in anchors}

    rows: List[ImportRow] = []
    for aid, (key, _conf, _src) in decided.items():
        anchor = by_id.get(aid)
        if anchor is None or key not in BY_KEY:
            continue                       # __SKIP__ / __UNKNOWN__ 不匯入

        raw = anchor.get("existing", "").strip()
        if not raw:
            continue
        # 勾選題存的是整串「■男　□女」，要取出被勾的那一項
        value = extractor.checked_option(raw) if anchor["kind"] == "checkbox" else raw
        if not value:
            continue

        ordinal = ordinals.get(aid, 0)
        current = str(matcher.get_value(profile, key, ordinal) or "")
        rows.append(ImportRow(
            anchor_id=aid, label=anchor["label"], field_key=key, ordinal=ordinal,
            current=current, incoming=value, default_checked=not current))

    rows.sort(key=lambda r: r.anchor_id)
    return rows

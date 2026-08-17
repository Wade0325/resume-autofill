"""流程編排：填寫（我的資料 → 空白履歷）與匯入（已填履歷 → 我的資料）。

API 層只管 HTTP，core 只管演算法，順序寫在這裡。
"""
from __future__ import annotations

import logging
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import actions, config, db
from .core import convert, document, llm, planner, reader, writer
from .core.document import Slot
from .core.schema import BY_KEY
from .schemas import (ImportPreviewOut, ImportRow, PlanItem, PlanOut, PlanStats)

log = logging.getLogger(__name__)


def job_dir(job_id: str) -> Path:
    return config.JOBS_DIR / job_id


def input_path(job_id: str) -> Path:
    """上傳的原檔。填寫一律是 .docx，匯入還收 104 履歷的 .pdf。"""
    pdf = job_dir(job_id) / "input.pdf"
    return pdf if pdf.exists() else job_dir(job_id) / "input.docx"


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


def _save_upload(job_id: str, content: bytes, suffix: str = ".docx") -> Path:
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    path = job_dir(job_id) / f"input{suffix}"
    path.write_bytes(content)
    return path


def _fail(update, work_id: str, filename: str, verb: str, doing: str,
          e: Exception) -> None:
    """兩個背景 worker 共用的失敗收尾：記 log、寫失敗原因、發使用者訊息。
    verb 用在開發者 log（分析／匯入），doing 用在給使用者的原因。"""
    if isinstance(e, llm.LlmUnavailable):
        log.warning("%s失敗 %s：%s", verb, filename, e)
        update(work_id, status="failed", stage="",
               error=f"模型還沒啟動，無法{doing}。請從右上角啟動模型後重新上傳")
        actions.problem("上傳履歷「%s」失敗：模型還沒啟動", filename)
    elif isinstance(e, llm.LlmCallFailed):
        # 模型活著但這次呼叫失敗（如文件超出上下文），叫使用者重啟模型只會鬼打牆
        log.warning("%s失敗 %s：%s", verb, filename, e)
        update(work_id, status="failed", stage="", error=f"無法{doing}：{e}")
        actions.problem("上傳履歷「%s」失敗：模型讀取失敗", filename)
    else:
        log.exception("%s失敗 %s", verb, filename)
        update(work_id, status="failed", stage="",
               error=f"無法解析這份文件：{e}")
        actions.problem("上傳履歷「%s」失敗：檔案無法解析", filename)


def analyze(filename: str, content: bytes) -> str:
    """收下檔案就回 job_id，分析在背景執行緒跑。

    真實履歷的分析要兩三分鐘，同步請求會讓前端乾等、一斷線結果就丟了。
    前端拿 job_id 輪詢 get_job_state() 看進度。
    """
    job_id = uuid.uuid4().hex[:12]
    _save_upload(job_id, content)
    log.info("上傳 %s (%.1f KB) job=%s", filename, len(content) / 1024, job_id)
    db.create_job(job_id, filename, status="processing")
    threading.Thread(target=_analyze_worker, args=(job_id, filename),
                     daemon=True).start()
    return job_id


def _analyze_worker(job_id: str, filename: str) -> None:
    src = input_path(job_id)
    t0 = time.perf_counter()
    try:
        # 先讀出這份文件已經有的值，才分得出哪些非空格子是使用者資料（可覆蓋）、
        # 哪些是表格印好的欄位名稱（不能碰）。
        # 空白範本（多數場景）沒有已填值，這一步 15~44 秒是白等——
        # 先用規則掃有沒有「值長相」的內容，沒有就整步跳過
        db.update_job(job_id, stage="讀取文件內容")
        parsed = document.ParsedDoc(str(src))
        text, slots = parsed.flatten()
        probe = document.MARKER_RE.sub("", text)
        existing: Dict[str, Any] = {}
        if not document.has_user_values(probe):
            log.info("看起來是空白範本，跳過既有值判讀")
        else:
            existing = reader.read(probe, config.LLM_HOST, config.LLM_MODEL)
        if existing:
            # 有已填值才需要重掃一次——這次把那些值標成可覆蓋的位置
            text, slots = parsed.flatten(_values_of(existing))
        fp = document.fingerprint(slots)
        cached = db.get_template(fp)
        log.info("解析完成 位置=%d 可覆蓋=%d 全文=%d字 fingerprint=%s 範本快取=%s",
                 len(slots),
                 sum(1 for s in slots if s.kind == "cell" and s.existing.strip()),
                 len(text), fp,
                 "命中" if cached else "未命中")

        # 先讓模型整份讀過，列出「這份表格要求填哪些欄位」。逐格判讀是拿
        # 一小段字問語意，看不見整體；哪些欄位這份表格根本沒問，要通篇讀過
        # 才知道。這份清單接著把逐格判讀的選項收斂到只剩它們（實測 83 個
        # 欄位縮到 42 個）。範本快取命中就整步跳過。
        form_fields: List[str] = []
        if not cached:
            db.update_job(job_id, stage="辨識表格欄位")
            form_fields = reader.list_fields(probe, config.LLM_HOST, config.LLM_MODEL)

        db.update_job(job_id, stage="辨識欄位對映" if not cached else "套用已學過的格式")
        headers = parsed.slot_headers(slots)
        decisions = planner.decide_by_anchor(
            parsed.table_texts(), slots, config.LLM_HOST, config.LLM_MODEL, cached,
            headers=headers, learned=db.get_kv("learned_labels") or {},
            allowed=form_fields or None)

        # 第二輪修正：只在有新錨定的格子時跑（純快取代表使用者確認過）。
        # 先做零成本的確定性對齊（白名單外格子、期間欄拆併），
        # 再由模型指認學經歷每一列對應清單第幾筆（分級列會錯位的根源）。
        if any(d.source != "cache" for d in decisions.values()):
            db.update_job(job_id, stage="覆核對映結果")
            profile = db.get_kv("profile") or {}
            decisions = planner.align_labels(slots, decisions, headers)
            decisions = planner.assign_rows(slots, decisions, profile, headers,
                                            config.LLM_HOST, config.LLM_MODEL)

        db.update_job(job_id, fingerprint=fp,
                      anchors=[s.to_dict() for s in slots],
                      decided={k: list(v) for k, v in decisions.items()},
                      form_fields=form_fields, status="analyzed", stage="")

        plan = _render(job_id, filename, bool(cached), slots, decisions, form_fields)
        log.info("比對完成 fill=%d skip=%d by_source=%s 耗時=%dms",
                 plan.stats.fill, plan.stats.skip, plan.stats.by_source,
                 int((time.perf_counter() - t0) * 1000))
        actions.record("上傳履歷「%s」成功", filename)
    except Exception as e:
        _fail(db.update_job, job_id, filename, "分析", "辨識欄位", e)


def _restore(job: Dict[str, Any]) -> Tuple[List[Slot], Dict[str, planner.Decision]]:
    """DB 裡的 JSON 還原成 Slot 與決策。"""
    slots = [Slot(**s) for s in job["anchors"]]
    decisions = {k: planner.Decision(*v) for k, v in job["decided"].items()}
    return slots, decisions


def get_job_state(job_id: str) -> Optional[Dict[str, Any]]:
    """輪詢用：processing 給階段、failed 給原因、好了給完整計畫。"""
    job = db.get_job(job_id)
    if not job:
        return None
    if job["status"] == "processing":
        return {"status": "processing", "stage": job.get("stage") or "準備中",
                "filename": job["filename"]}
    if job["status"] == "failed":
        return {"status": "failed", "error": job.get("error") or "分析失敗",
                "filename": job["filename"]}
    return {"status": "ready", "plan": get_plan(job_id)}


def get_plan(job_id: str) -> Optional[PlanOut]:
    job = db.get_job(job_id)
    if not job:
        return None
    slots, decisions = _restore(job)
    return _render(job_id, job["filename"],
                   bool(db.get_template(job["fingerprint"])), slots, decisions,
                   job.get("form_fields"))


def preview_docx(job_id: str, which: str) -> Optional[bytes]:
    """左右對照的兩份文件，交給前端直接渲染。

    filled 每次重算——使用者剛改過對映就要看到新結果，而且一律標黃底，
    才看得出資料落在哪一格；下載的成品不標。
    """
    job = db.get_job(job_id)
    if not job:
        return None
    src = input_path(job_id)
    if which == "original":
        return src.read_bytes()

    slots, decisions = _restore(job)
    ops, _ = planner.build_plan(slots, db.get_kv("profile") or {}, decisions)
    with tempfile.TemporaryDirectory(prefix="preview_") as tmp:
        filled = Path(tmp) / "filled.docx"
        writer.apply_ops(str(src), str(filled), ops, highlight=True)
        return filled.read_bytes()


def apply_fixes(job_id: str, fixes: List[Tuple[str, str]]) -> Optional[PlanOut]:
    """套用使用者修正。只改決策再重算，不會再呼叫模型。"""
    job = db.get_job(job_id)
    if not job:
        return None
    slots, decisions = _restore(job)
    valid = {s.id for s in slots}

    # 先整批驗證再套用：中途才發現非法值的話，前面幾筆已經播報了
    # 「修改成功」、學習字典也動了，但整批決策不會落庫
    for slot_id, field_key in fixes:
        if slot_id not in valid:
            raise ValueError(f"位置不存在：{slot_id}")
        if field_key not in BY_KEY and field_key not in ("__SKIP__", "__UNKNOWN__"):
            raise ValueError(f"未知欄位代碼：{field_key}")

    lessons = db.get_kv("learned_labels") or {}
    lessons_dirty = False
    for slot_id, field_key in fixes:
        previous = decisions.get(slot_id)
        old = previous.field_key if previous else ""
        label = previous.label if previous else ""
        decisions[slot_id] = planner.Decision(
            field_key, previous.ordinal if previous else 0, "manual", label)
        # 這是日後改進提示詞的唯一依據
        log.info("使用者修正 %s：%s → %s", slot_id, old or "(未決定)", field_key)
        actions.record("修改欄位「%s」", label or slot_id)
        lessons_dirty |= _learn_label(lessons, label, field_key)
    if lessons_dirty:
        db.put_kv("learned_labels", lessons)

    db.update_job(job_id, decided={k: list(v) for k, v in decisions.items()})
    return _render(job_id, job["filename"],
                   bool(db.get_template(job["fingerprint"])), slots, decisions,
                   job.get("form_fields"))


def _learn_label(lessons: Dict[str, Any], label: str, field_key: str) -> bool:
    """把使用者修正回饋成跨表格的「標籤→欄位」知識。回傳有沒有改動。

    只學內建對照表外、squash 後 ≥3 字的標籤：「姓名」「電話」這種泛用
    短標籤在不同區塊指不同欄位（緊急連絡人的姓名≠本人姓名），
    學成全域反而誤傷。改成「找不到對應」＝遺忘，是反悔的出口。
    """
    sq = planner._squash(label)
    if not sq or len(sq) < 3 or sq in planner.LABEL_MAP:
        return False
    if field_key == "__UNKNOWN__":
        if sq not in lessons:
            return False
        del lessons[sq]
        actions.record("忘掉標籤「%s」學過的對應", label)
        return True
    if lessons.get(sq, {}).get("field_key") == field_key:
        return False
    lessons[sq] = {"label": label, "field_key": field_key}
    shown = "不填" if field_key == "__SKIP__" else BY_KEY[field_key].label
    actions.record("學會標籤「%s」→「%s」，之後所有表格都適用", label, shown)
    return True


def write_output(job_id: str) -> Optional[Dict[str, Any]]:
    job = db.get_job(job_id)
    if not job:
        return None
    slots, decisions = _restore(job)
    ops, _ = planner.build_plan(slots, db.get_kv("profile") or {}, decisions)

    t0 = time.perf_counter()
    # 下載的成品不標黃底：要核對填在哪一格，看網頁上的左右對照就好，
    # 黃底留在成品裡使用者還得自己去 Word 清掉
    result = writer.apply_ops(str(input_path(job_id)), str(output_path(job_id)), ops)
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
                    for sid, (key, ordinal, _src, label) in decisions.items()})
    db.put_template(job["fingerprint"], mapping, source_name=job["filename"])
    log.info("範本已學習 fingerprint=%s 位置=%d", job["fingerprint"], len(mapping))

    return {"job_id": job_id, "written": result["written"], "failed": result["failed"]}


def _render(job_id: str, filename: str, cached: bool, slots: List[Slot],
            decisions: Dict[str, Any], form_fields: Optional[List[str]] = None) -> PlanOut:
    ops, skipped = planner.build_plan(slots, db.get_kv("profile") or {}, decisions)

    items = [_item(o, "fill") for o in ops] + [_item(s, "skip") for s in skipped]
    items.sort(key=lambda i: i.slot_id)

    by_source: Dict[str, int] = {}
    for o in ops:
        by_source[o.source] = by_source.get(o.source, 0) + 1

    return PlanOut(
        job_id=job_id, filename=filename,
        template_cached=cached, llm_available=llm.available(config.LLM_HOST),
        stats=PlanStats(slots=len(slots), fill=len(ops), skip=len(skipped),
                        by_source=by_source),
        form_fields=[BY_KEY[k].label for k in (form_fields or []) if k in BY_KEY],
        items=items)


def _item(op, status: str) -> PlanItem:
    return PlanItem(
        slot_id=op.slot.id, label=op.label, kind=op.slot.kind,
        field_key=op.field_key, value=str(op.value),
        existing=op.slot.existing, source=op.source,
        status=status, note=op.note)


def analyze_import(filename: str, content: bytes) -> Dict[str, Any]:
    """收下檔案就回 import_id，讀取在背景執行緒跑（與填寫的 analyze 同一套理由：
    模型讀一份履歷要幾分鐘，同步請求會讓切頁的使用者丟失結果）。"""
    import_id = uuid.uuid4().hex[:12]
    _save_upload(import_id, content, ".pdf" if filename.lower().endswith(".pdf") else ".docx")
    log.info("匯入上傳 %s (%.1f KB) import=%s", filename, len(content) / 1024, import_id)
    db.create_import(import_id, filename)
    threading.Thread(target=_import_worker, args=(import_id, filename),
                     daemon=True).start()
    return {"import_id": import_id, "status": "processing", "filename": filename}


def _import_worker(import_id: str, filename: str) -> None:
    src = input_path(import_id)
    t0 = time.perf_counter()
    try:
        is_pdf = src.suffix == ".pdf"
        db.update_import(import_id, stage="讀取文件內容")
        pdf_bytes = src.read_bytes() if is_pdf else b""
        text = convert.pdf_to_text(pdf_bytes) if is_pdf else document.text_only(str(src))

        # .docx 攤平後本來就帶著表格結構，附截圖反而讓模型改去讀圖——實測純文字
        # 比較準（欄位標題被當成值、姓名被當成職稱那類錯誤明顯變多）
        images: List[bytes] = []
        if is_pdf and llm.supports_vision(config.LLM_HOST):
            try:
                db.update_import(import_id, stage="擷取頁面截圖")
                images = convert.pdf_to_page_pngs(pdf_bytes)
                log.info("視覺模式：附 %d 頁截圖", len(images))
            except Exception as e:
                log.warning("截圖產生失敗，改用純文字讀取：%s", e)
                images = []

        db.update_import(import_id, stage="模型讀取資料中")
        try:
            extracted = reader.read(text, config.LLM_HOST, config.LLM_MODEL, images=images)
        except llm.LlmError:
            if not images:
                raise
            # 視覺呼叫失敗不該讓整次匯入陪葬，退回純文字再試一次
            log.warning("視覺讀取失敗，退回純文字重試")
            extracted = reader.read(text, config.LLM_HOST, config.LLM_MODEL)

        db.update_import(import_id, extracted=extracted, status="ready", stage="")
        rows = _import_rows(extracted)
        log.info("匯入讀取完成 全文=%d字 欄位=%d 需覆蓋=%d 耗時=%dms",
                 len(text), len(rows), sum(1 for r in rows if not r.default_checked),
                 int((time.perf_counter() - t0) * 1000))
        actions.record("上傳履歷「%s」成功，等待確認匯入", filename)
    except Exception as e:
        _fail(db.update_import, import_id, filename, "匯入", "讀取資料", e)


def import_source(import_id: str) -> Optional[bytes]:
    if db.get_import(import_id) is None or not input_path(import_id).exists():
        return None
    return input_path(import_id).read_bytes()


def get_import(import_id: str) -> Optional[Dict[str, Any]]:
    """輪詢用：processing 給階段、failed 給原因、好了給完整預覽。"""
    record = db.get_import(import_id)
    if not record:
        return None
    if record["status"] == "processing":
        return {"status": "processing", "stage": record.get("stage") or "準備中",
                "filename": record["filename"]}
    if record["status"] == "failed":
        return {"status": "failed", "error": record.get("error") or "讀取失敗",
                "filename": record["filename"]}
    preview = ImportPreviewOut(import_id=import_id, filename=record["filename"],
                               rows=_import_rows(record["extracted"]))
    return {"status": "ready", "preview": preview.model_dump()}


def apply_import(import_id: str, row_ids: List[str]) -> Optional[int]:
    record = db.get_import(import_id)
    if not record or record["status"] != "ready":
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
        # 舊紀錄的值可能混進 {{id}} 位置標記，顯示與寫入前都剝掉
        text = document.MARKER_RE.sub("", str(value)).strip()
        if field_key not in BY_KEY or not text:
            return
        current = str(planner.get_value(profile, field_key, ordinal) or "")
        rows.append(ImportRow(
            row_id=f"{field_key}#{ordinal}", field_key=field_key, ordinal=ordinal,
            current=current, incoming=text, default_checked=not current))

    for key, value in extracted.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    for sub, sub_value in item.items():
                        add(f"{key}[].{sub}", index, sub_value)
        else:
            add(key, 0, value)

    # 多筆資料「同一筆聚在一起」:第 1 筆的公司/職稱/到職…看完,再換第 2 筆。
    # 同一筆內的欄位照定義表的順序(與「我的資料」表單一致),不是字母序——
    # 字母序會讓 end(離職)跑到 start(到職)前面
    def root(key: str) -> str:
        return key.split("[].")[0].split(".", 1)[0]

    order = {key: i for i, key in enumerate(BY_KEY)}
    group: Dict[str, int] = {}
    for i, key in enumerate(BY_KEY):
        group.setdefault(root(key), i)
    rows.sort(key=lambda r: (group.get(root(r.field_key), len(order)),
                             r.ordinal,
                             order.get(r.field_key, len(order))))
    return rows

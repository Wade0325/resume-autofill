"""流程編排：填寫（我的資料 → 空白履歷）與匯入（已填履歷 → 我的資料）。

API 層只管 HTTP，core 只管演算法，順序寫在這裡。
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import actions, config, db
from .core import convert, document, filler, llm, planner, reader, writer
from .core.document import Slot
from .core.schema import BY_KEY
from .schemas import (ImportPreviewOut, ImportRow, PlanItem, PlanOut, PlanStats)

log = logging.getLogger(__name__)


_WORK_ID_RE = re.compile(r"[0-9a-f]{12}")      # analyze／analyze_import 發的代碼


def job_dir(job_id: str) -> Path:
    # 代碼會接進檔案路徑，不是自己發的格式一律不認（API 層已先擋，這裡是最後一道）
    if not _WORK_ID_RE.fullmatch(job_id):
        raise ValueError(f"不認得的工作代碼：{job_id!r}")
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


# ---------------------------------------------------------------------------
# 兩條填寫路線
# ---------------------------------------------------------------------------
# classic：規則錨定 ＋ 純文字模型（planner.py）。不需要視覺投影檔，看不見版面，
#          靠攤平後的全文與列首欄首判斷。
# vlm    ：讓模型看著版面示意圖決定每一格放哪一項（filler.py）。對沒看過的排版準得多
#          （AT-1 重評：classic 50/67、vlm 66/67），速度差不多（120 秒 vs 104～113 秒）；
#          研究用的三份考題逐格全對。
# 兩條都留著：使用者的機器不一定掛得動視覺投影檔，掛不動就自動退回 classic。
ENGINES = ("classic", "vlm")

# filler 的位置種類換成前端看得懂的說法——前端靠 kind 分辨「插字」還是「整格覆蓋」
_VLM_KIND = {"box": "checkbox", "gap": "print", "append": "print",
             "line": "print", "blank": "cell"}
_NOT_FILLED = ("__SKIP__", "__UNKNOWN__")


def current_engine(vision: bool) -> str:
    """使用者選過就照選的；沒選過時，模型看得到圖（vision）就用 vlm，看不到才用 classic。
    vision 由呼叫端探一次傳進來——模型沒開時每探一次要等半秒。"""
    engine = db.get_kv("engine")
    if engine in ENGINES:
        return engine
    return "vlm" if vision else "classic"


def _split_key(key: str) -> Tuple[str, int]:
    """filler 的 experience[2].salary → 產品的 (experience[].salary, 2)。

    產品一路上用「樣板代碼 ＋ 第幾筆」兩個欄位表示清單資料（schema、前端下拉、
    planner 都是），filler 則把序號寫在代碼裡。介面在這裡換一次，兩邊都不必改。
    """
    m = re.match(r"(\w+)\[(\d+)\]\.(.+)$", key)
    return (f"{m.group(1)}[].{m.group(3)}", int(m.group(2))) if m else (key, 0)


def _join_key(field_key: str, ordinal: int) -> str:
    return field_key.replace("[]", f"[{ordinal}]") if "[]" in field_key else field_key


# 清單型的資料（education、experience…），填寫頁要知道每一種有幾筆才列得出「第幾筆」
_LIST_ROOTS = sorted({k.split("[]")[0] for k in BY_KEY if "[]" in k})


def _entries(profile: Dict[str, Any]) -> Dict[str, int]:
    return {root: len(profile.get(root) or []) for root in _LIST_ROOTS}


def _slot_order(slot_id: str) -> List[Any]:
    """對映清單照位置排，數字照大小比：直接比字串的話 tbl0.r10 排在 tbl0.r2 前面、
    c12 排在 c2 前面，使用者對著表格一格一格看時會找不到。
    切出來一定是「字、數字、字、數字…」交替，同一個位置型別相同，比較不會出錯。"""
    return [int(t) if i % 2 else t for i, t in enumerate(re.split(r"(\d+)", slot_id))]


def _vlm_label(slot: Any) -> str:
    """表格上印在這個位置旁邊的字。左邊欄名優先，沒有就取上面欄名。"""
    return (document.squash(slot.cell.row_head)
            or document.squash(slot.cell.col_head))[:40]


def _tick_basis(value: str) -> str:
    """勾選是對著哪個值判斷的：存值的雜湊、不存值本身。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else ""


def _values_for(decisions: Dict[str, planner.Decision],
                profile: Dict[str, Any]) -> Dict[str, str]:
    """每一格目前對到的值（跟 filler 寫的時候同一份攤平）。"""
    values = filler.fields_of(profile)
    return {sid: values.get(_join_key(d.field_key, d.ordinal), "")
            for sid, d in decisions.items() if d.field_key and d.field_key not in _NOT_FILLED}


def _live_ticks(recorded: Dict[str, Tuple[bool, Optional[str]]],
                decisions: Dict[str, planner.Decision],
                profile: Dict[str, Any]) -> Dict[str, bool]:
    """模型判斷過的勾，只留還算數的：當初判斷用的值跟現在一樣。

    勾選題「資料『無』對『□否』」是模型的語意判斷，學過的格式把它存下來重用；可是
    我的資料改了（未婚改已婚、聲明事項改答案），照舊勾就勾錯了。對不上的交回 filler 照
    字面與同義詞判斷——勾錯比留白糟。沒記依據的（這個機制之前學的格式）照舊沿用，
    下次下載時補記。沒對到欄位的格子（手動改成不填）不留勾。
    """
    now = _values_for(decisions, profile)
    return {sid: tick for sid, (tick, basis) in recorded.items()
            if sid in now and (basis is None or basis == _tick_basis(now[sid]))}


def _vlm_anchors(slots: List[Any], decisions: Dict[str, planner.Decision],
                 ticks: Dict[str, bool], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    now = _values_for(decisions, profile)
    return [{"id": s.id, "kind": s.kind, "label": _vlm_label(s), "tick": ticks.get(s.id),
             "basis": _tick_basis(now.get(s.id, "")) if s.id in ticks else None}
            for s in slots]


def _vlm_restore(job: Dict[str, Any]) -> Tuple[List[Any], Dict[str, planner.Decision],
                                               Dict[str, bool]]:
    """vlm 的位置牽著 python-docx 的段落物件，存不進資料庫——照原檔重新解析一次。
    解析純粹是程式，同一份文件跑幾次結果都一樣，約 0.2 秒。"""
    _doc, _form, slots = filler.parse(input_path(job["id"]))
    decisions = {k: planner.Decision(*v) for k, v in job["decided"].items()}
    recorded = {a["id"]: (a["tick"], a.get("basis"))
                for a in job["anchors"] if a.get("tick") is not None}
    return slots, decisions, _live_ticks(recorded, decisions, db.get_kv("profile") or {})


def _vlm_assignment(decisions: Dict[str, planner.Decision]) -> Dict[str, str]:
    return {sid: _join_key(d.field_key, d.ordinal) for sid, d in decisions.items()
            if d.field_key and d.field_key not in _NOT_FILLED}


def _vlm_worker(job_id: str, filename: str) -> None:
    src = input_path(job_id)
    t0 = time.perf_counter()
    try:
        db.update_job(job_id, stage="盤點可寫位置")
        _doc, _form, slots = filler.parse(src)
        # 指紋帶上引擎：兩條路認出來的位置編號不一樣，學過的對映不能混用
        fp = "vlm:" + document.fingerprint(slots)
        cached = db.get_template(fp)
        log.info("解析完成 位置=%d fingerprint=%s 範本快取=%s",
                 len(slots), fp, "命中" if cached else "未命中")

        profile = db.get_kv("profile") or {}
        if cached:
            db.update_job(job_id, stage="套用已學過的格式")
            decisions = {sid: planner.Decision(m["field_key"], m.get("ordinal", 0),
                                               "cache", m.get("label", ""))
                         for sid, m in cached.items()}
            ticks = _live_ticks({sid: (m["tick"], m.get("basis")) for sid, m in cached.items()
                                 if m.get("tick") is not None}, decisions, profile)
        else:
            db.update_job(job_id, stage="模型看版面判讀每一格")
            draft = filler.analyze(src, profile, config.LLM_HOST, config.LLM_MODEL)
            by_id = {s.id: s for s in draft.slots}
            decisions = {}
            for sid, key in draft.assignment.items():
                field_key, ordinal = _split_key(key)
                decisions[sid] = planner.Decision(field_key, ordinal, "model",
                                                  _vlm_label(by_id[sid]))
            ticks = draft.ticks

        db.update_job(job_id, fingerprint=fp,
                      anchors=_vlm_anchors(slots, decisions, ticks, profile),
                      decided={k: list(v) for k, v in decisions.items()},
                      status="analyzed", stage="")

        plan = _vlm_render(job_id, filename, bool(cached), slots, decisions, ticks)
        log.info("比對完成 fill=%d skip=%d by_source=%s 耗時=%dms",
                 plan.stats.fill, plan.stats.skip, plan.stats.by_source,
                 int((time.perf_counter() - t0) * 1000))
        actions.record("上傳履歷「%s」成功", filename)
    except Exception as e:
        _fail(db.update_job, job_id, filename, "分析", "辨識欄位", e)


def _vlm_render(job_id: str, filename: str, cached: bool, slots: List[Any],
                decisions: Dict[str, planner.Decision],
                ticks: Dict[str, bool]) -> PlanOut:
    """把 filler 的判讀結果換成前端那張對映清單。

    值只是拿來顯示的：實際寫進去的字由 filler 決定（日期會拆進「＿年＿月」、
    西元換民國、勾選框寫的是打勾），所以這裡顯示原始值就好。
    """
    profile = db.get_kv("profile") or {}
    # filler 自己算出來的值（年資、英文姓氏）planner 不認得，先查 filler 那一份
    values = filler.fields_of(profile)
    items, by_source = [], {}
    for slot in slots:
        d = decisions.get(slot.id)
        value = ""
        if d and d.field_key and d.field_key not in _NOT_FILLED:
            value = (values.get(_join_key(d.field_key, d.ordinal))
                     or str(planner.get_value(profile, d.field_key, d.ordinal) or ""))
        fill = bool(value)
        if fill:
            by_source[d.source] = by_source.get(d.source, 0) + 1
        note = ""
        # 勾選框，以及欄名是選項的空格子（學歷表「日間」底下那格打記號）
        if slot.kind == "box" or slot.id in ticks:
            note = "打勾" if ticks.get(slot.id) else ("不勾" if fill else "")
        items.append(PlanItem(
            slot_id=slot.id, label=_vlm_label(slot),
            kind=_VLM_KIND.get(slot.kind, slot.kind),
            field_key=(d.field_key if d else ""), value=value, existing="",
            source=(d.source if d else ""), status="fill" if fill else "skip",
            note=note, ordinal=(d.ordinal if d else 0)))
    items.sort(key=lambda i: _slot_order(i.slot_id))
    fill = sum(1 for i in items if i.status == "fill")
    return PlanOut(
        job_id=job_id, filename=filename,
        template_cached=cached, llm_available=llm.available(config.LLM_HOST),
        stats=PlanStats(slots=len(slots), fill=fill, skip=len(items) - fill,
                        by_source=by_source),
        form_fields=[], items=items, entries=_entries(profile))


def _save_upload(job_id: str, content: bytes, suffix: str = ".docx") -> Path:
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    path = job_dir(job_id) / f"input{suffix}"
    path.write_bytes(content)
    return path


def _fail(update, work_id: str, filename: str, verb: str, doing: str,
          e: Exception) -> None:
    """兩個背景 worker 共用的失敗收尾：記 log、寫失敗原因、發使用者訊息。
    verb 用在開發者 log（分析／匯入），doing 用在給使用者的原因。
    檔名常帶著本人姓名，只出現在日誌頁（使用者要看得出是哪個檔）；開發者 log 記工作代碼。"""
    if isinstance(e, llm.LlmUnavailable):
        log.warning("%s失敗 %s：%s", verb, work_id, e)
        update(work_id, status="failed", stage="",
               error=f"模型還沒啟動，無法{doing}。請從右上角啟動模型後重新上傳")
        actions.problem("上傳履歷「%s」失敗：模型還沒啟動", filename)
    elif isinstance(e, llm.LlmCallFailed):
        # 模型活著但這次呼叫失敗（如文件超出上下文），叫使用者重啟模型只會鬼打牆
        log.warning("%s失敗 %s：%s", verb, work_id, e)
        update(work_id, status="failed", stage="", error=f"無法{doing}：{e}")
        actions.problem("上傳履歷「%s」失敗：模型讀取失敗", filename)
    else:
        log.exception("%s失敗 %s", verb, work_id)
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
    vision = llm.supports_vision(config.LLM_HOST)
    engine = current_engine(vision)
    if engine == "vlm" and not vision:
        # 視覺版看不到版面就退化成一般文字模型，不如走本來就不看圖的那條路
        log.warning("模型沒掛視覺投影檔，這份改用 classic")
        engine = "classic"
    log.info("上傳 %.1f KB job=%s engine=%s", len(content) / 1024, job_id, engine)
    db.create_job(job_id, filename, status="processing", engine=engine)
    worker = _vlm_worker if engine == "vlm" else _analyze_worker
    threading.Thread(target=worker, args=(job_id, filename), daemon=True).start()
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
            headers=headers, allowed=form_fields or None)

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
    cached = bool(db.get_template(job["fingerprint"]))
    if job.get("engine") == "vlm":
        slots, decisions, ticks = _vlm_restore(job)
        return _vlm_render(job_id, job["filename"], cached, slots, decisions, ticks)
    slots, decisions = _restore(job)
    return _render(job_id, job["filename"], cached, slots, decisions,
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

    profile = db.get_kv("profile") or {}
    with tempfile.TemporaryDirectory(prefix="preview_") as tmp:
        filled = Path(tmp) / "filled.docx"
        if job.get("engine") == "vlm":
            _slots, decisions, ticks = _vlm_restore(job)
            filler.write(src, filled, _vlm_assignment(decisions), ticks, profile,
                         highlight=True)
        else:
            slots, decisions = _restore(job)
            ops, _ = planner.build_plan(slots, profile, decisions)
            writer.apply_ops(str(src), str(filled), ops, highlight=True)
        return filled.read_bytes()


def apply_fixes(job_id: str,
                fixes: List[Tuple[str, str, Optional[int]]]) -> Optional[PlanOut]:
    """套用使用者修正。只改決策再重算，不會再呼叫模型。
    ordinal 是清單欄位用第幾筆（第 2 所學校）；沒給就沿用這一格原本的。"""
    job = db.get_job(job_id)
    if not job:
        return None
    vlm = job.get("engine") == "vlm"
    ticks: Dict[str, bool] = {}
    if vlm:
        slots, decisions, ticks = _vlm_restore(job)
    else:
        slots, decisions = _restore(job)
    valid = {s.id for s in slots}

    # 先整批驗證再套用：中途才發現非法值的話，前面幾筆已經播報了
    # 「修改成功」，但整批決策不會落庫
    for slot_id, field_key, _ordinal in fixes:
        if slot_id not in valid:
            raise ValueError(f"位置不存在：{slot_id}")
        if field_key not in BY_KEY and field_key not in ("__SKIP__", "__UNKNOWN__"):
            raise ValueError(f"未知欄位代碼：{field_key}")

    for slot_id, field_key, ordinal in fixes:
        previous = decisions.get(slot_id)
        old = previous.field_key if previous else ""
        label = previous.label if previous else ""
        if ordinal is None:
            ordinal = previous.ordinal if previous else 0
        decisions[slot_id] = planner.Decision(field_key, ordinal, "manual", label)
        # 這是日後改進提示詞的唯一依據
        log.info("使用者修正 %s：%s → %s#%d", slot_id, old or "(未決定)", field_key, ordinal)
        actions.record("修改欄位「%s」", label or slot_id)

    db.update_job(job_id, decided={k: list(v) for k, v in decisions.items()})
    cached = bool(db.get_template(job["fingerprint"]))
    if vlm:
        # 使用者改過的格子不沿用模型當初的勾：改成不填就不勾，改成別的欄位就照
        # 那個欄位的值字面判斷（以前舊的勾照樣套用，改了也勾著）
        for slot_id, _key, _ordinal in fixes:
            ticks.pop(slot_id, None)
        db.update_job(job_id, anchors=_vlm_anchors(slots, decisions, ticks,
                                                   db.get_kv("profile") or {}))
        return _vlm_render(job_id, job["filename"], cached, slots, decisions, ticks)
    return _render(job_id, job["filename"], cached, slots, decisions,
                   job.get("form_fields"))


def write_output(job_id: str) -> Optional[Dict[str, Any]]:
    job = db.get_job(job_id)
    if not job:
        return None
    vlm = job.get("engine") == "vlm"
    profile = db.get_kv("profile") or {}
    ticks: Dict[str, bool] = {}
    t0 = time.perf_counter()
    # 下載的成品不標黃底：要核對填在哪一格，看網頁上的左右對照就好，
    # 黃底留在成品裡使用者還得自己去 Word 清掉
    if vlm:
        _slots, decisions, ticks = _vlm_restore(job)
        written = filler.write(input_path(job_id), output_path(job_id),
                               _vlm_assignment(decisions), ticks, profile)
        result = {"written": written, "failed": 0, "fail": []}
    else:
        slots, decisions = _restore(job)
        ops, _ = planner.build_plan(slots, profile, decisions)
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
    # 勾選框還要記「勾不勾」：資料「無」對選項「否」是語意判斷，光有欄位代碼補不回來；
    # 連同判斷依據（值的雜湊）一起記，資料改了下次就知道這個勾不算數（見 _live_ticks）
    now = _values_for(decisions, profile) if vlm else {}
    mapping = dict(db.get_template(job["fingerprint"]))
    mapping.update({sid: {"field_key": key, "ordinal": ordinal, "label": label,
                          **({"tick": ticks[sid], "basis": _tick_basis(now.get(sid, ""))}
                             if sid in ticks else {})}
                    for sid, (key, ordinal, _src, label) in decisions.items()})
    db.put_template(job["fingerprint"], mapping, source_name=job["filename"])
    log.info("範本已學習 fingerprint=%s 位置=%d", job["fingerprint"], len(mapping))

    return {"job_id": job_id, "written": result["written"], "failed": result["failed"]}


def _render(job_id: str, filename: str, cached: bool, slots: List[Slot],
            decisions: Dict[str, Any], form_fields: Optional[List[str]] = None) -> PlanOut:
    profile = db.get_kv("profile") or {}
    ops, skipped = planner.build_plan(slots, profile, decisions)

    items = [_item(o, "fill") for o in ops] + [_item(s, "skip") for s in skipped]
    items.sort(key=lambda i: _slot_order(i.slot_id))

    by_source: Dict[str, int] = {}
    for o in ops:
        by_source[o.source] = by_source.get(o.source, 0) + 1

    return PlanOut(
        job_id=job_id, filename=filename,
        template_cached=cached, llm_available=llm.available(config.LLM_HOST),
        stats=PlanStats(slots=len(slots), fill=len(ops), skip=len(skipped),
                        by_source=by_source),
        form_fields=[BY_KEY[k].label for k in (form_fields or []) if k in BY_KEY],
        items=items, entries=_entries(profile))


def _item(op, status: str) -> PlanItem:
    return PlanItem(
        slot_id=op.slot.id, label=op.label, kind=op.slot.kind,
        field_key=op.field_key, value=str(op.value),
        existing=op.slot.existing, source=op.source,
        status=status, note=op.note, ordinal=op.ordinal)


def analyze_import(filename: str, content: bytes) -> Dict[str, Any]:
    """收下檔案就回 import_id，讀取在背景執行緒跑（與填寫的 analyze 同一套理由：
    模型讀一份履歷要幾分鐘，同步請求會讓切頁的使用者丟失結果）。"""
    import_id = uuid.uuid4().hex[:12]
    _save_upload(import_id, content, ".pdf" if filename.lower().endswith(".pdf") else ".docx")
    log.info("匯入上傳 %.1f KB import=%s", len(content) / 1024, import_id)
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


def apply_import(import_id: str, row_ids: List[str]) -> Optional[List[str]]:
    """把勾選的列寫進我的資料，回傳實際寫到的「欄位代碼#第幾筆」。"""
    record = db.get_import(import_id)
    if not record or record["status"] != "ready":
        return None
    rows = {r.row_id: r for r in _import_rows(record["extracted"])}
    selected = [rows[i] for i in row_ids if i in rows]

    profile = db.get_kv("profile") or {}
    # 新增的那幾筆只建有勾到欄位的，照順序往前補——一欄都沒勾的不留一筆空白的
    remap: Dict[Tuple[str, int], int] = {}
    for root in {_list_root(r.field_key) for r in selected if r.entry == "new"}:
        used = sorted({r.ordinal for r in selected
                       if r.entry == "new" and _list_root(r.field_key) == root})
        base = len(profile.get(root) or [])
        remap.update({(root, o): base + i for i, o in enumerate(used)})
    changed = []
    for row in selected:
        ordinal = remap.get((_list_root(row.field_key), row.ordinal), row.ordinal)
        planner.set_value(profile, row.field_key, row.incoming, ordinal)
        changed.append(f"{row.field_key}#{ordinal}")
    db.put_kv("profile", profile)

    # 只記欄位代碼——incoming 全是個資
    log.info("匯入寫入 選取=%d 欄位=%s", len(changed),
             ",".join(sorted({c.split("#")[0] for c in changed})))
    actions.record("匯入履歷「%s」成功", record["filename"])
    return changed


def _list_root(field_key: str) -> str:
    return field_key.split("[].")[0] if "[]." in field_key else ""


# 匯入的一筆學經歷是「我的資料」裡的哪一筆：看名稱（學校、公司…）——以前看順序，
# 履歷第一筆是別家公司時，它的薪資、離職原因會補進你現有那一筆的空欄位。
# 名稱一樣還要次要欄位不衝突：同一所學校的學士與碩士是兩筆，同一家公司離職又回鍋也是兩筆
_IDENTITY = {"education": ("school", ("degree", "start")),
             "experience": ("company", ("start",)),
             "certificate": ("name", ()),
             "family": ("name", ()),
             "reference": ("name", ())}
_PEOPLE = {"family", "reference"}     # 人名要整個一樣：「王明」不是「王明德」
_NAME_NOISE_RE = re.compile(r"股份有限公司|有限公司|\(股\)|[\s,.。、・·()\-]")
# 學位只比程度：「學士」「大學」是同一級，「碩士」「研究所」也是
_DEGREE_LEVELS = (("博士", "phd", "doctor"), ("碩士", "研究所", "master", "mba"),
                  ("大學", "學士", "二技", "四技", "bachelor"), ("專科", "五專", "二專", "三專"),
                  ("高中", "高職", "high school"), ("國中",))


def _name_key(text: str) -> str:
    """比對名稱用：全半形、大小寫、臺／台、公司後綴與標點都不算差別。"""
    text = unicodedata.normalize("NFKC", text or "").lower().replace("臺", "台")
    return _NAME_NOISE_RE.sub("", text)


def _same_name(a: str, b: str, whole: bool = False) -> bool:
    """公司、學校的簡稱算同一個（「台灣大學」「國立臺灣大學」）；人名 whole 要整個一樣。"""
    x, y = _name_key(a), _name_key(b)
    if len(x) < 2 or len(y) < 2:
        return False
    return x == y if whole else (x in y or y in x)


def _degree_level(text: str) -> Optional[int]:
    text = unicodedata.normalize("NFKC", text).lower()
    return next((i for i, words in enumerate(_DEGREE_LEVELS)
                 if any(w in text for w in words)), None)


def _no_conflict(field: str, a: Any, b: Any) -> bool:
    """兩邊都有值的次要欄位要一致；認不出來的寫法不算衝突。
    日期只比西元年（「2016/9」「2016年09月」是同一年），學位只比程度。"""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return True
    if field == "start":
        ya, yb = re.search(r"\d{4}", a), re.search(r"\d{4}", b)
        return not (ya and yb) or ya.group() == yb.group()
    if field == "degree":
        la, lb = _degree_level(a), _degree_level(b)
        return la is None or lb is None or la == lb
    return document.squash(a) == document.squash(b)


def _entry_targets(root: str, incoming: List[Any],
                   existing: List[Any]) -> List[Tuple[int, str, str]]:
    """每一筆匯入的資料寫進第幾筆：(第幾筆, merge／new, 對上的那一筆的名稱)。
    對不上、或沒有名稱的就新增一筆——寧可多一筆讓使用者刪，不把別家的資料補進來。"""
    name_field, secondary = _IDENTITY.get(root, ("", ()))
    taken, out, new = set(), [], 0
    for item in incoming:
        item = item if isinstance(item, dict) else {}
        name = str(item.get(name_field) or "") if name_field else ""
        hit = next((i for i, row in enumerate(existing)
                    if i not in taken and isinstance(row, dict)
                    and _same_name(name, str(row.get(name_field) or ""), root in _PEOPLE)
                    and all(_no_conflict(f, item.get(f), row.get(f)) for f in secondary)),
                   None) if name else None
        if hit is not None:
            taken.add(hit)
            out.append((hit, "merge", str(existing[hit].get(name_field) or "")))
        else:
            out.append((len(existing) + new, "new", name))
            new += 1
    return out


def _import_rows(extracted: Dict[str, Any]) -> List[ImportRow]:
    profile = db.get_kv("profile") or {}
    rows: List[ImportRow] = []

    def add(field_key: str, ordinal: int, value: Any, entry: str = "",
            entry_name: str = "") -> None:
        # 舊紀錄的值可能混進 {{id}} 位置標記，顯示與寫入前都剝掉
        text = document.MARKER_RE.sub("", str(value)).strip()
        if field_key not in BY_KEY or not text:
            return
        current = "" if entry == "new" else str(planner.get_value(profile, field_key, ordinal) or "")
        rows.append(ImportRow(
            row_id=f"{field_key}#{ordinal}", field_key=field_key, ordinal=ordinal,
            current=current, incoming=text, default_checked=not current,
            entry=entry, entry_name=entry_name))

    for key, value in extracted.items():
        if isinstance(value, list):
            targets = _entry_targets(key, value, profile.get(key) or [])
            for item, (ordinal, entry, entry_name) in zip(value, targets):
                if isinstance(item, dict):
                    for sub, sub_value in item.items():
                        add(f"{key}[].{sub}", ordinal, sub_value, entry, entry_name)
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

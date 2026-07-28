"""健康檢查與欄位白名單。"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from .. import config, db
from ..core import llm
from ..core.schema import FIELDS, SENSITIVE_KEYS
from ..schemas import FieldSpecOut, HealthOut, LlmStatus

log = logging.getLogger(__name__)
router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    available = llm.available(config.LLM_HOST)
    try:
        db.get_kv("settings")
        db_ok = True
    except Exception:
        log.exception("資料庫檢查失敗")
        db_ok = False
    return HealthOut(
        db=db_ok,
        llm=LlmStatus(available=available,
                      backend="llamacpp" if available else "null",
                      host=config.LLM_HOST, model=config.LLM_MODEL))


@router.get("/fields", response_model=list[FieldSpecOut])
def fields() -> list[FieldSpecOut]:
    """給前端的下拉選單用；也是模型能選的完整白名單。"""
    return [FieldSpecOut(key=f.key, label=f.label, kind=f.kind,
                         choices=f.choices, sensitive=f.key in SENSITIVE_KEYS,
                         derived=f.derived)
            for f in FIELDS]

"""健康檢查與欄位白名單。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, db, service
from ..core import llm
from ..core.schema import FIELDS
from ..schemas import FieldSpecOut, HealthOut, LlmStatus

log = logging.getLogger(__name__)
router = APIRouter(tags=["meta"])


class EngineIn(BaseModel):
    engine: str


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    available = llm.available(config.LLM_HOST)
    try:
        db.get_kv("profile")   # 只是確認資料庫打得開，讀哪個 key 都行
        db_ok = True
    except Exception:
        log.exception("資料庫檢查失敗")
        db_ok = False
    return HealthOut(
        db=db_ok,
        llm=LlmStatus(available=available,
                      backend="llamacpp" if available else "null",
                      host=config.LLM_HOST, model=config.LLM_MODEL))


@router.get("/engine")
def read_engine() -> dict:
    """現在用哪一條路填表，以及這台機器的模型看不看得到圖。"""
    return {"engine": service.current_engine(),
            "engines": list(service.ENGINES),
            "vision": llm.supports_vision(config.LLM_HOST)}


@router.post("/engine")
def set_engine(body: EngineIn) -> dict:
    if body.engine not in service.ENGINES:
        raise HTTPException(422, f"不認得的引擎：{body.engine}")
    db.put_kv("engine", body.engine)
    log.info("填寫引擎切換為 %s", body.engine)
    return {"engine": body.engine}


@router.get("/fields", response_model=list[FieldSpecOut])
def fields() -> list[FieldSpecOut]:
    """給前端的下拉選單用；也是模型能選的完整白名單。"""
    return [FieldSpecOut(key=f.key, label=f.label, kind=f.kind,
                         choices=f.choices, derived=f.derived)
            for f in FIELDS]

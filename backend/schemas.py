"""HTTP 請求與回應模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class FieldSpecOut(BaseModel):
    key: str
    label: str
    kind: str
    choices: List[str] = []
    aliases: List[str] = []
    sensitive: bool = False


class LlmStatus(BaseModel):
    available: bool
    backend: str
    host: str
    model: str


class HealthOut(BaseModel):
    api: Literal["ok"] = "ok"
    db: bool
    llm: LlmStatus


class SettingsIn(BaseModel):
    min_confidence: float = Field(0.60, ge=0.0, le=1.0)
    allow_sensitive: bool = False
    highlight_filled: bool = True


class PlanItem(BaseModel):
    anchor_id: str
    label: str
    kind: str
    options: List[str] = []
    field_key: str
    value: str
    confidence: float
    source: str                       # cache | rule | fuzzy | llm | manual
    status: Literal["fill", "skip"]
    note: str = ""


class PlanStats(BaseModel):
    anchors: int
    fill: int
    skip: int
    by_source: Dict[str, int]


class PlanOut(BaseModel):
    job_id: str
    filename: str
    fingerprint: str
    template_cached: bool
    llm_available: bool
    stats: PlanStats
    items: List[PlanItem]


class MappingFix(BaseModel):
    anchor_id: str
    field_key: str


class MappingsIn(BaseModel):
    fixes: List[MappingFix]


class OutputOut(BaseModel):
    job_id: str
    written: int
    failed: int
    learned: int
    download_url: str


ProfileIn = Dict[str, Any]      # profile 結構由 core.schema 定義，這層不重複驗證

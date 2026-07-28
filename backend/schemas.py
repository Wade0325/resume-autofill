"""HTTP 請求與回應模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class FieldSpecOut(BaseModel):
    key: str
    label: str
    kind: str
    choices: List[str] = []
    sensitive: bool = False
    derived: bool = False             # 由其他欄位合成，個人資料表單不顯示


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
    slot_id: str
    label: str = ""                   # 表格上印在這格旁邊的字，由模型抄回來
    kind: str
    options: List[str] = []
    field_key: str
    ordinal: int = 0                  # 第幾筆學歷／經歷
    value: str
    existing: str = ""                # 文件原本就有的內容，非空代表這一格會被覆蓋
    confidence: float
    source: str                       # cache | model | manual
    status: Literal["fill", "skip"]
    note: str = ""


class PlanStats(BaseModel):
    slots: int
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
    slot_id: str
    field_key: str


class MappingsIn(BaseModel):
    fixes: List[MappingFix]


class OutputOut(BaseModel):
    job_id: str
    written: int
    failed: int
    learned: int
    download_url: str


class ImportRow(BaseModel):
    row_id: str                       # "欄位代碼#序號"，模型每欄只給一個值所以必定唯一
    field_key: str
    ordinal: int                      # 第幾筆學歷／經歷
    current: str                      # 我的資料現在的值
    incoming: str                     # 從履歷讀到的值
    default_checked: bool             # current 為空才預設勾選


class ImportPreviewOut(BaseModel):
    import_id: str
    filename: str
    rows: List[ImportRow]


class ImportApplyIn(BaseModel):
    row_ids: List[str]


class ImportApplyOut(BaseModel):
    applied: int


ProfileIn = Dict[str, Any]      # profile 結構由 core.schema 定義，這層不重複驗證

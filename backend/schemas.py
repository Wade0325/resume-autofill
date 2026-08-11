"""HTTP 請求與回應模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel


class FieldSpecOut(BaseModel):
    key: str
    label: str
    kind: str
    choices: List[str] = []
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


class PlanItem(BaseModel):
    slot_id: str
    label: str = ""                   # 表格上印在這格旁邊的字，機械抽取自列首／欄首
    kind: str
    field_key: str
    value: str
    existing: str = ""                # 文件原本就有的內容，非空代表這一格會被覆蓋
    source: str                       # rule | learned | model | cache | manual
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


class LogEntry(BaseModel):
    time: str                         # 年月日時分秒
    level: str
    module: str
    message: str


ProfileIn = Dict[str, Any]      # profile 結構由 core.schema 定義，這層不重複驗證

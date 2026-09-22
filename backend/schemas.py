"""HTTP 請求與回應模型。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from . import __version__


class FieldSpecOut(BaseModel):
    key: str
    label: str
    kind: str
    choices: List[str] = []
    derived: bool = False             # 由其他欄位合成，個人資料表單不顯示
    per_job: bool = False             # 每份工作自己一個值，填寫頁的「這次應徵」面板填


class LlmStatus(BaseModel):
    available: bool
    backend: str
    host: str
    model: str


class HealthOut(BaseModel):
    api: Literal["ok"] = "ok"          # 啟動器靠這個認出是自己的後端，別改
    version: str = __version__
    db: bool
    llm: LlmStatus


class PlanItem(BaseModel):
    slot_id: str
    label: str = ""                   # 表格上印在這格旁邊的字，機械抽取自列首／欄首
    kind: str
    field_key: str
    value: str
    existing: str = ""                # 文件原本就有的內容，非空代表這一格會被覆蓋
    source: str                       # rule | model | cache | manual
    status: Literal["fill", "skip"]
    note: str = ""
    ordinal: int = 0                  # 清單欄位（學歷、經歷…）用第幾筆，從 0 起算


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
    # 模型看過版面後認出「這份表格要填哪些欄位」，給使用者對照用（欄位名稱）
    form_fields: List[str] = []
    items: List[PlanItem]
    # 我的資料裡每一種清單有幾筆（education: 3），填寫頁的「第幾筆」選單照這個列
    entries: Dict[str, int] = {}
    # 這份工作的「這次應徵」：應徵職務、工作地點…只算這一份，不進「我的資料」
    apply: Dict[str, str] = {}


class MappingFix(BaseModel):
    slot_id: str
    field_key: str
    ordinal: Optional[int] = Field(None, ge=0)   # 清單欄位用第幾筆；不給就沿用原本的


class MappingsIn(BaseModel):
    fixes: List[MappingFix]


class LearnedFormatOut(BaseModel):
    """學過的一份格式。fingerprint 是結構指紋＋表格上印的字。"""
    fingerprint: str
    engine: str
    source_name: str = ""
    slots: int
    updated_at: str


class FingerprintIn(BaseModel):
    fingerprint: str


class TypedIn(BaseModel):
    """對映清單裡直接把某一格改成自己打的字；value 給空字串就改回自動判斷的值。"""
    slot_id: str
    value: str


class JobHistoryOut(BaseModel):
    """填寫紀錄的一列。保留期內都還能重新下載。"""
    job_id: str
    filename: str
    status: str                       # processing | analyzed | failed
    engine: str
    error: str = ""
    created_at: str
    downloadable: bool


class ApplyIn(BaseModel):
    """「這次應徵」：應徵職務、工作地點…只算這一份工作，不進「我的資料」。"""
    values: Dict[str, str]


class OutputOut(BaseModel):
    job_id: str
    written: int
    failed: int


class BatchStatusOut(BaseModel):
    """批次面板的一列。輪詢用，所以不帶計畫內容。"""
    job_id: str
    filename: str
    status: str                       # processing | analyzed | failed | missing
    stage: str = ""                   # 排隊中（前面還有 N 份）／逐格判讀 第 n／m 批
    error: str = ""
    downloadable: bool
    fill: int = 0                     # 會填幾格（分析完才有）


class BatchIdsIn(BaseModel):
    job_ids: List[str]


class BatchOutputOut(BaseModel):
    """整批套用的結果。一份失敗不影響其他份，所以逐份回報。"""
    job_id: str
    filename: str
    ok: bool
    error: str = ""
    written: int = 0


class ImportRow(BaseModel):
    row_id: str                       # "欄位代碼#序號"，模型每欄只給一個值所以必定唯一
    field_key: str
    ordinal: int                      # 第幾筆學歷／經歷
    current: str                      # 我的資料現在的值
    incoming: str                     # 從履歷讀到的值
    default_checked: bool             # current 為空才預設勾選
    # 多筆資料（學經歷…）這一筆怎麼放：merge＝補進名稱對得上的那一筆，new＝新增一筆
    entry: Literal["", "merge", "new"] = ""
    entry_name: str = ""              # 對上的那一筆的名稱（學校、公司…）


class ImportPreviewOut(BaseModel):
    import_id: str
    filename: str
    rows: List[ImportRow]
    note: str = ""                    # 給使用者的提醒（掃描檔：沒有原文可比對，請自己核對）
    has_source: bool = True           # 有沒有原檔可以顯示（貼上的文字沒有）


class ImportApplyIn(BaseModel):
    row_ids: List[str]


class ImportApplyOut(BaseModel):
    applied: int
    changed: List[str] = []           # 實際寫到的「欄位代碼#第幾筆」，我的資料頁拿來標示


class ProfileVersionOut(BaseModel):
    id: int
    reason: str                       # 被什麼換掉：save | import | restore | file
    created_at: str
    changed: int                      # 跟現在的我的資料相比有幾個欄位不一樣


class LogEntry(BaseModel):
    time: str                         # 年月日時分秒
    level: str
    module: str
    message: str


ProfileIn = Dict[str, Any]      # profile 結構由 core.schema 定義，這層不重複驗證


class WebFormPick(BaseModel):
    """清單上勾選的一筆，連同使用者在清單上改過的值。"""
    id: str
    values: Dict[str, str] = Field(default_factory=dict)
    alts: Dict[str, bool] = Field(default_factory=dict)   # 「現任職位」「永久有效」勾不勾


class WebFormRunIn(BaseModel):
    items: List[WebFormPick]

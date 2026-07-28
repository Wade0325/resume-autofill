"""標準履歷欄位定義——整個系統的單一事實來源。

使用者資料存成這份結構；模型只能從 FIELD_KEYS 裡挑，發明不了欄位。
要加欄位就在 FIELDS 加一筆，前端表單與模型提示都會自動跟上。
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class FieldSpec:
    key: str                 # 正規欄位路徑，例如 "contact.mobile"
    label: str
    kind: str = "text"       # text | date | money | choice | longtext | list
    choices: List[str] = field(default_factory=list)
    hint: str = ""           # 給模型的一句話說明
    derived: bool = False    # 值由其他欄位合成，不出現在個人資料表單


FIELDS: List[FieldSpec] = [
    # ---------- 基本資料 ----------
    FieldSpec("basic.name_zh", "中文姓名", hint="申請人的中文全名"),
    FieldSpec("basic.name_en", "英文姓名"),
    FieldSpec("basic.gender", "性別", kind="choice", choices=["男", "女"]),
    FieldSpec("basic.birthday", "出生年月日", kind="date", hint="西元 YYYY-MM-DD"),
    FieldSpec("basic.age", "年齡"),
    FieldSpec("basic.marital_status", "婚姻狀況", kind="choice", choices=["未婚", "已婚"]),
    FieldSpec("basic.military", "兵役狀況", kind="choice", choices=["役畢", "免役", "未役", "替代役", "不適用"]),
    # 出生地、身分證字號、血型、身高、體重、身心障礙刻意不收：
    # 履歷表就算印了這些格子也不填。欄位不存在 → 白名單裡沒有 → 模型無從對映，
    # 比「收了再靠設定擋住」更徹底。

    # ---------- 聯絡方式 ----------
    FieldSpec("contact.mobile", "行動電話"),
    FieldSpec("contact.phone_home", "住家電話"),
    FieldSpec("contact.email", "電子郵件"),
    # 地址只留一個。履歷表常見「戶籍地址」與「通訊地址」兩格，兩格都對映到這裡，
    # 對絕大多數人來說填的本來就是同一個地址。
    FieldSpec("contact.address_mailing", "地址"),

    # ---------- 應徵資訊 ----------
    FieldSpec("job.applied_position", "應徵職務"),
    FieldSpec("job.expected_salary", "希望待遇", kind="money", hint="月薪金額"),
    FieldSpec("job.available_date", "可到職日", kind="date"),

    # ---------- 學歷（list，會展開成 education[0].xxx） ----------
    FieldSpec("education[].school", "學校名稱", kind="list"),
    FieldSpec("education[].department", "科系", kind="list"),
    FieldSpec("education[].degree", "學位", kind="list"),
    FieldSpec("education[].start", "入學年月", kind="list"),
    FieldSpec("education[].end", "畢業年月", kind="list"),
    # 只印一欄「就學期間」的表格用這個，值由入學與畢業合成
    FieldSpec("education[].period", "就學期間", kind="list", derived=True),
    FieldSpec("education[].status", "畢業狀態", kind="list"),

    # ---------- 經歷 ----------
    FieldSpec("experience[].company", "公司名稱", kind="list"),
    FieldSpec("experience[].title", "職稱", kind="list"),
    FieldSpec("experience[].start", "到職年月", kind="list"),
    FieldSpec("experience[].end", "離職年月", kind="list"),
    FieldSpec("experience[].period", "任職期間", kind="list", derived=True),
    FieldSpec("experience[].description", "工作內容", kind="list"),
    FieldSpec("experience[].salary", "薪資", kind="list"),
    FieldSpec("experience[].leave_reason", "離職原因", kind="list"),

    # ---------- 專長 ----------
    FieldSpec("skills.languages", "語文能力", kind="longtext"),
    FieldSpec("skills.certificates", "專業證照", kind="longtext"),
    FieldSpec("skills.computer", "電腦技能", kind="longtext"),
    FieldSpec("skills.driver_license", "駕照", kind="choice",
              choices=["無", "普通重型機車", "普通小型車", "普通小型車＋普通重型機車",
                       "職業小型車", "職業大貨車", "職業大客車"]),
    FieldSpec("skills.specialty", "專長", kind="longtext"),

    # ---------- 緊急聯絡人 ----------
    FieldSpec("emergency.name", "緊急聯絡人姓名"),
    FieldSpec("emergency.relation", "緊急聯絡人關係"),
    FieldSpec("emergency.phone", "緊急聯絡人電話"),

    # ---------- 長文 ----------
    FieldSpec("autobiography", "自傳", kind="longtext"),
]

# 給 LLM 用的白名單。SKIP 代表「這格不是要填的空白」，NONE 代表「找不到對應資料」。
SPECIAL_KEYS = ["__SKIP__", "__UNKNOWN__"]
FIELD_KEYS = [f.key for f in FIELDS] + SPECIAL_KEYS

BY_KEY = {f.key: f for f in FIELDS}

# 高敏感欄位：預設不自動填，除非 config 明確開啟
SENSITIVE_KEYS = {"basic.marital_status"}


def describe_fields(include_special: bool = True) -> str:
    lines = []
    for f in FIELDS:
        extra = f" 選項={f.choices}" if f.choices else ""
        hint = f" — {f.hint}" if f.hint else ""
        lines.append(f"- {f.key}: {f.label}{extra}{hint}")
    if include_special:
        lines.append("- __SKIP__: 這個位置不是求職者要填的（表頭、說明文字、公司自用欄）")
        lines.append("- __UNKNOWN__: 是欄位，但清單裡沒有對應項目")
    return "\n".join(lines)


# 由起訖兩欄合成的欄位。表格有時印一欄「就學期間」，有時印「入學年月／畢業年月」，
# 使用者只需要填後者。
DERIVED_FROM = {
    "education[].period": ("education[].start", "education[].end"),
    "experience[].period": ("experience[].start", "experience[].end"),
}

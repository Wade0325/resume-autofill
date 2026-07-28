"""
標準履歷欄位定義 (Canonical Profile Schema)
------------------------------------------------
這是整個系統的「單一事實來源」(single source of truth)：

1. 使用者資料只填一次，存成符合這份 schema 的 profile.json
2. LLM 只能從 FIELD_KEYS 這個白名單裡挑欄位，不能自由發明欄位名稱
3. ALIASES 提供規則比對第一層，可攔截 70~90% 的常見標籤，省掉 LLM 呼叫

新增欄位時：在 FIELDS 加一筆即可，其他模組會自動跟上。
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class FieldSpec:
    key: str                 # 正規欄位路徑，例如 "contact.mobile"
    label: str               # 人類可讀名稱
    aliases: List[str] = field(default_factory=list)   # 表單上可能出現的寫法
    kind: str = "text"       # text | date | choice | longtext | list
    choices: List[str] = field(default_factory=list)   # kind == "choice" 時的選項
    hint: str = ""           # 給 LLM 的一句話說明


FIELDS: List[FieldSpec] = [
    # ---------- 基本資料 ----------
    FieldSpec("basic.name_zh", "中文姓名",
              ["姓名", "中文姓名", "本名", "申請人姓名", "應徵者姓名", "名字", "姓 名"],
              hint="申請人的中文全名"),
    FieldSpec("basic.name_en", "英文姓名",
              ["英文姓名", "英文名", "護照英文姓名", "English Name", "Name (English)"]),
    FieldSpec("basic.gender", "性別",
              ["性別", "生理性別", "Gender", "Sex"],
              kind="choice", choices=["男", "女"]),
    FieldSpec("basic.birthday", "出生年月日",
              ["出生年月日", "生日", "出生日期", "出生", "Date of Birth", "DOB"],
              kind="date", hint="西元 YYYY-MM-DD"),
    FieldSpec("basic.age", "年齡", ["年齡", "實歲", "Age"]),
    FieldSpec("basic.marital_status", "婚姻狀況",
              ["婚姻狀況", "婚姻", "已婚未婚", "Marital Status"],
              kind="choice", choices=["未婚", "已婚"]),
    FieldSpec("basic.military", "兵役狀況",
              ["兵役", "兵役狀況", "服役情形", "役別", "兵役狀態"],
              kind="choice", choices=["役畢", "免役", "未役", "替代役", "不適用"]),
    # 出生地、身分證字號、血型、身高、體重、身心障礙刻意不收：
    # 履歷表就算印了這些格子也不填。欄位不存在 → 白名單裡沒有 → 模型無從對映，
    # 比「收了再靠設定擋住」更徹底。

    # ---------- 聯絡方式 ----------
    FieldSpec("contact.mobile", "行動電話",
              ["行動電話", "手機", "手機號碼", "行動電話號碼", "Mobile", "Cell Phone"]),
    FieldSpec("contact.phone_home", "住家電話",
              ["住家電話", "戶籍電話", "家用電話", "室內電話", "Home Phone"]),
    FieldSpec("contact.phone_contact", "聯絡電話",
              ["聯絡電話", "日間聯絡電話", "白天電話", "Tel", "電話"]),
    FieldSpec("contact.email", "電子郵件",
              ["電子郵件", "電子信箱", "E-mail", "Email", "電郵", "信箱"]),
    FieldSpec("contact.line_id", "LINE ID", ["LINE ID", "Line", "通訊軟體帳號"]),
    FieldSpec("contact.address_registered", "戶籍地址",
              ["戶籍地址", "戶籍住址", "戶籍", "Registered Address"]),
    FieldSpec("contact.address_mailing", "通訊地址",
              ["通訊地址", "聯絡地址", "現居地址", "郵寄地址", "住址", "地址",
               "Mailing Address", "Address"]),

    # ---------- 應徵資訊 ----------
    FieldSpec("job.applied_position", "應徵職務",
              ["應徵職務", "應徵職位", "應徵部門", "申請職務", "希望職務", "職務名稱",
               "Position Applied"]),
    FieldSpec("job.expected_salary", "希望待遇",
              ["希望待遇", "期望待遇", "希望薪資", "要求待遇", "Expected Salary"]),
    FieldSpec("job.available_date", "可到職日",
              ["可到職日", "可上班日", "最快到職日", "預計到職日", "Available Date"],
              kind="date"),
    FieldSpec("job.source", "應徵管道",
              ["應徵管道", "得知管道", "訊息來源", "資料來源", "如何得知本職缺"]),
    FieldSpec("job.referrer", "介紹人",
              ["介紹人", "推薦人", "內部推薦人", "Referrer"]),

    # ---------- 學歷（list，會展開成 education[0].xxx） ----------
    FieldSpec("education[].school", "學校名稱",
              ["學校名稱", "校名", "畢業學校", "學校", "School"], kind="list"),
    FieldSpec("education[].department", "科系",
              ["科系", "系所", "主修", "科別", "Major", "Department"], kind="list"),
    FieldSpec("education[].degree", "學位",
              ["學位", "學歷", "程度", "Degree"], kind="list"),
    FieldSpec("education[].period", "就學期間",
              ["就學期間", "起訖年月", "修業期間", "在學期間"], kind="list"),
    FieldSpec("education[].status", "畢業狀態",
              ["畢肄業", "畢業狀態", "肄業", "是否畢業"], kind="list"),

    # ---------- 經歷 ----------
    FieldSpec("experience[].company", "公司名稱",
              ["公司名稱", "服務機構", "任職公司", "機構名稱", "Company"], kind="list"),
    FieldSpec("experience[].title", "職稱",
              ["職稱", "職位", "擔任職務", "Title", "Position"], kind="list"),
    FieldSpec("experience[].period", "任職期間",
              ["任職期間", "服務期間", "起訖年月", "工作期間"], kind="list"),
    FieldSpec("experience[].description", "工作內容",
              ["工作內容", "主要職掌", "工作職掌", "業務內容", "Job Description"],
              kind="list"),
    FieldSpec("experience[].salary", "薪資",
              ["薪資", "待遇", "月薪"], kind="list"),
    FieldSpec("experience[].leave_reason", "離職原因",
              ["離職原因", "離職理由", "異動原因"], kind="list"),

    # ---------- 專長 ----------
    FieldSpec("skills.languages", "語文能力",
              ["語文能力", "外語能力", "語言能力", "Languages"], kind="longtext"),
    FieldSpec("skills.certificates", "專業證照",
              ["專業證照", "證照", "執照", "資格證明", "Certificates"], kind="longtext"),
    FieldSpec("skills.computer", "電腦技能",
              ["電腦技能", "電腦專長", "資訊能力", "軟體專長", "Computer Skills"],
              kind="longtext"),
    FieldSpec("skills.driver_license", "駕照",
              ["駕照", "駕駛執照", "Driver License"]),
    FieldSpec("skills.specialty", "專長",
              ["專長", "個人專長", "技能", "Skills"], kind="longtext"),

    # ---------- 緊急聯絡人 ----------
    FieldSpec("emergency.name", "緊急聯絡人姓名",
              ["緊急聯絡人", "緊急聯絡人姓名", "緊急連絡人"]),
    FieldSpec("emergency.relation", "緊急聯絡人關係",
              ["關係", "與本人關係", "稱謂"]),
    FieldSpec("emergency.phone", "緊急聯絡人電話",
              ["緊急聯絡電話", "緊急聯絡人電話", "緊急連絡電話"]),

    # ---------- 長文 ----------
    FieldSpec("autobiography", "自傳",
              ["自傳", "個人簡介", "自我介紹", "Autobiography", "Self Introduction"],
              kind="longtext"),
    FieldSpec("motivation", "應徵動機",
              ["應徵動機", "求職動機", "為何應徵本公司"], kind="longtext"),
]

# 給 LLM 用的白名單。SKIP 代表「這格不是要填的空白」，NONE 代表「找不到對應資料」。
SPECIAL_KEYS = ["__SKIP__", "__UNKNOWN__"]
FIELD_KEYS = [f.key for f in FIELDS] + SPECIAL_KEYS

BY_KEY = {f.key: f for f in FIELDS}

# 高敏感欄位：預設不自動填，除非 config 明確開啟
SENSITIVE_KEYS = {"basic.marital_status"}


def normalize_label(text: str) -> str:
    """把標籤正規化，讓『姓　名：』『姓名(中文)』『姓名 *』都能對上『姓名』。"""
    if not text:
        return ""
    drop = " \u3000\t\n:：*※()（）［］[]{}<>《》〈〉.。,，、/-_＿"
    out = "".join(ch for ch in text if ch not in drop)
    return out.lower()


ALIAS_INDEX = {}
for _f in FIELDS:
    for _a in [_f.label] + _f.aliases:
        ALIAS_INDEX.setdefault(normalize_label(_a), _f.key)


def describe_fields_for_llm() -> str:
    """產生一段精簡的欄位說明，塞進 LLM prompt。"""
    lines = []
    for f in FIELDS:
        extra = f" 選項={f.choices}" if f.choices else ""
        hint = f" — {f.hint}" if f.hint else ""
        lines.append(f"- {f.key}: {f.label}{extra}{hint}")
    lines.append("- __SKIP__: 這個位置不是要填寫的欄位（例如表頭、說明文字、公司自用欄）")
    lines.append("- __UNKNOWN__: 是欄位，但上面清單裡沒有對應項目")
    return "\n".join(lines)

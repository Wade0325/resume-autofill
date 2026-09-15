"""標準履歷欄位定義——整個系統的單一事實來源。

使用者資料存成這份結構；模型只能從 FIELD_KEYS 裡挑，發明不了欄位。
要加欄位就在 FIELDS 加一筆，前端表單與模型提示都會自動跟上。
"""

from dataclasses import dataclass, field
from typing import Collection, List, Optional


@dataclass(frozen=True)
class FieldSpec:
    key: str                 # 正規欄位路徑，例如 "contact.mobile"
    label: str
    kind: str = "text"       # text | date | money | choice | longtext | list
    choices: List[str] = field(default_factory=list)
    hint: str = ""           # 只給模型看，不會出現在表單上
    derived: bool = False    # 值由其他欄位合成，不出現在個人資料表單


FIELDS: List[FieldSpec] = [
    # 私密欄位（身分證字號、身分別…）一樣開放：值要不要填由使用者自己決定，
    # 系統只提供服務。
    FieldSpec("basic.name_zh", "中文姓名", hint="申請人的中文全名"),
    FieldSpec("basic.name_en", "英文姓名"),
    FieldSpec("basic.name_passport", "護照全名", hint="與護照相同的英文全名"),
    # 表格要「英文名＋羅馬拼音姓氏」時用。值由護照全名取出，不必另外填
    FieldSpec("basic.surname_en", "英文姓氏", derived=True),
    FieldSpec("basic.national_id", "身分證字號"),
    FieldSpec("basic.gender", "性別", kind="choice", choices=["男", "女"]),
    FieldSpec("basic.birthday", "出生年月日", kind="date",
              hint="西元年月日，例如 1996年04月15日。只寫「28歲」是年齡，不是生日"),
    # 表格有的印民國、有的印西元。存的是哪一種要講清楚，填寫時才知道要不要換算；
    # 表格沒指定就填西元
    FieldSpec("basic.birthday_era", "生日曆制", kind="choice", choices=["西元", "民國"],
              hint="上面那個出生年月日填的是西元還是民國"),
    FieldSpec("basic.age", "年齡"),
    FieldSpec("basic.nationality", "國籍", hint="例如 中華民國"),
    FieldSpec("basic.birthplace", "出生地"),
    FieldSpec("basic.height", "身高", hint="公分數字，例如 175"),
    FieldSpec("basic.weight", "體重", hint="公斤數字，例如 68"),
    FieldSpec("basic.blood_type", "血型", kind="choice", choices=["A", "B", "O", "AB"]),
    FieldSpec("basic.health", "健康狀況", kind="choice", choices=["優", "良", "可", "差"]),
    FieldSpec("basic.marital_status", "婚姻狀況", kind="choice", choices=["未婚", "已婚"]),
    FieldSpec("basic.military", "兵役狀況", kind="choice", choices=["役畢", "免役", "未役", "替代役", "不適用"]),
    FieldSpec("basic.military_exempt_reason", "免役原因",
              hint="免役的原因本身，例如 體位不合格。不要填「免役」兩個字"),
    FieldSpec("basic.identity_category", "身分別", kind="choice",
              choices=["無", "身心障礙", "原住民"], hint="表格上的身分別勾選欄"),
    FieldSpec("basic.transport", "交通工具", kind="choice",
              choices=["汽車", "機車", "大眾交通工具", "其他"]),
    FieldSpec("basic.hobbies", "興趣", hint="休閒興趣，例如 羽球、桌球"),

    FieldSpec("contact.mobile", "行動電話"),
    FieldSpec("contact.phone_home", "住家電話"),
    FieldSpec("contact.email", "電子郵件"),
    # 表格常見「戶籍地址」「通訊地址」兩格；只印一格「地址」的對映到通訊地址。
    FieldSpec("contact.address_mailing", "通訊地址"),
    FieldSpec("contact.address_household", "戶籍地址"),

    # 應徵職務刻意不收：每間公司都不一樣，存了也只會填錯，留白讓使用者手寫
    FieldSpec("job.expected_salary", "希望待遇", kind="money", hint="月薪金額"),
    FieldSpec("job.expected_salary_year", "期望年薪", kind="money", hint="年薪金額"),
    FieldSpec("job.available_date", "可到職日", kind="date", hint="西元年月日，例如 2026年09月01日"),
    FieldSpec("job.recruit_channel", "招募管道", hint="從哪裡得知職缺，例如 104人力銀行"),

    # education[] 這種 key 會展開成 education[0].xxx
    FieldSpec("education[].school", "學校名稱", kind="list"),
    FieldSpec("education[].department", "科系", kind="list"),
    FieldSpec("education[].degree", "學位", kind="list", hint="例如 大學、碩士、專科"),
    FieldSpec("education[].start", "入學年月", kind="date", hint="西元年月，例如 2014年09月"),
    FieldSpec("education[].end", "畢業年月", kind="date", hint="西元年月，例如 2018年06月"),
    # 只印一欄「就學期間」的表格用這個，值由入學與畢業合成
    FieldSpec("education[].period", "就學期間", kind="list", derived=True),
    FieldSpec("education[].status", "畢業狀態", kind="list", hint="畢業或肄業"),
    FieldSpec("education[].division", "日夜間部", kind="list", hint="只有寫日間部／夜間部／進修部時才填"),
    FieldSpec("education[].club", "社團活動", kind="list"),

    FieldSpec("experience[].company", "公司名稱", kind="list"),
    FieldSpec("experience[].department", "部門", kind="list",
              hint="部門名稱，例如 研發部。職稱不要填在這裡"),
    FieldSpec("experience[].title", "職稱", kind="list"),
    FieldSpec("experience[].start", "到職年月", kind="date", hint="西元年月，例如 2019年03月"),
    FieldSpec("experience[].end", "離職年月", kind="date", hint="西元年月，例如 2023年08月"),
    FieldSpec("experience[].period", "任職期間", kind="list", derived=True),
    # 由到職與離職年月算出來，頭尾兩個月都算（2023年7月～2026年4月＝2年10個月）
    FieldSpec("experience[].tenure", "年資", kind="list", derived=True),
    FieldSpec("experience[].description", "工作內容", kind="list"),
    # 只存固定月薪的金額：表單絕大多數只印一格「月薪」，
    # 津貼獎金那些拆項刻意不收，整坨照抄會把單格表單填得一塌糊塗
    FieldSpec("experience[].salary", "月薪", kind="list",
              hint="固定月薪的金額數字，例如 52,000。津貼、獎金不要"),
    FieldSpec("experience[].is_supervisor", "擔任主管", kind="choice", choices=["是", "否"]),
    FieldSpec("experience[].supervisor_title", "報告對象職稱", kind="list",
              hint="當時直屬主管的職稱，例如 研發部經理"),
    FieldSpec("experience[].leave_reason", "離職原因", kind="list"),

    FieldSpec("skills.languages", "語文能力", kind="longtext"),
    # 表格通常分開問「哪一個語言」與「程度到哪」，合在一句話裡就拆不出來
    FieldSpec("skills.language", "語言", hint="例如 英文、日文"),
    FieldSpec("skills.language_level", "語文程度", hint="例如 中等、尚可、精通"),
    FieldSpec("skills.certificates", "專業證照", kind="longtext"),
    FieldSpec("skills.computer", "電腦技能", kind="longtext"),
    FieldSpec("skills.driver_license", "駕照", kind="choice",
              choices=["無", "普通重型機車", "普通小型車", "普通小型車＋普通重型機車",
                       "職業小型車", "職業大貨車", "職業大客車"]),
    FieldSpec("skills.specialty", "專長", kind="longtext"),

    # 表格只印一格「專業證照」時用 skills.certificates；
    # 印成證照名稱／機構／字號的表格用這組多筆欄位
    FieldSpec("certificate[].name", "證照名稱", kind="list"),
    FieldSpec("certificate[].issuer", "考試機構", kind="list", hint="發證或考試的單位"),
    FieldSpec("certificate[].number_date", "證照字號與取得日期", kind="list"),

    # 標籤刻意加「家人」前綴：表格印的「姓名」「職業」太通用，
    # 若直接當標籤會在確定性對齊時搶走別區同名的格子。
    FieldSpec("family[].relation", "家人稱謂", kind="list", hint="例如 父、母、兄"),
    FieldSpec("family[].name", "家人姓名", kind="list"),
    FieldSpec("family[].age", "家人年齡", kind="list"),
    FieldSpec("family[].occupation", "家人職業", kind="list"),
    FieldSpec("family[].company", "家人服務機關", kind="list"),
    FieldSpec("family[].contact", "家人住址電話", kind="list"),

    # 標籤同樣加前綴，理由同家庭狀況
    FieldSpec("reference[].name", "諮詢人姓名", kind="list"),
    FieldSpec("reference[].company", "諮詢人公司", kind="list"),
    FieldSpec("reference[].title", "諮詢人職稱", kind="list"),
    FieldSpec("reference[].phone", "諮詢人電話", kind="list"),
    FieldSpec("reference[].location", "諮詢人公司所在地", kind="list", hint="縣市即可"),
    FieldSpec("reference[].relation", "諮詢人關係", kind="list", hint="與本人的關係，例如 直屬主管"),

    FieldSpec("emergency.name", "緊急聯絡人姓名"),
    FieldSpec("emergency.relation", "緊急聯絡人關係"),
    FieldSpec("emergency.phone", "緊急聯絡人電話"),

    FieldSpec("declaration.relatives_in_company", "親友任職於應徵公司", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.other_positions", "於其他公司擔任負責人或董監事", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.china_investment", "大陸地區投資業務", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.non_compete", "與前公司簽有競業條款", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.ip_ownership", "擁有相關智慧財產權或專門技術", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.criminal_record", "曾因刑事犯罪遭偵查或起訴", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.wanted", "遭任何國家通緝", kind="choice", choices=["無", "有"]),
    FieldSpec("declaration.infectious_disease", "曾感染重大傳染病", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.drug_use", "曾吸食毒品", kind="choice", choices=["無", "有"]),
    FieldSpec("declaration.dismissed", "曾遭免職或開除", kind="choice", choices=["無", "有"]),
    FieldSpec("declaration.forged_documents", "持用不實證件或文件", kind="choice",
              choices=["無", "有"]),
    FieldSpec("declaration.debt", "負債狀況", kind="choice", choices=["無", "有"]),
    FieldSpec("declaration.disability_certificate", "領有身心障礙手冊或曾患重大傷病",
              kind="choice", choices=["無", "有"]),

    FieldSpec("autobiography", "自傳", kind="longtext"),
]

# 給 LLM 用的白名單。__SKIP__ 代表「這格不是要填的空白」，__UNKNOWN__ 代表「找不到對應資料」。
SPECIAL_KEYS = ["__SKIP__", "__UNKNOWN__"]
FIELD_KEYS = [f.key for f in FIELDS] + SPECIAL_KEYS

BY_KEY = {f.key: f for f in FIELDS}


def describe_fields(include_special: bool = True, skip_derived: bool = False,
                    mark: Optional[Collection[str]] = None) -> str:
    """mark 給定時，在那些欄位後面加★——通篇讀過覺得這份表格有問的欄位。
    只是提示不是過濾：清單漏掉的欄位仍然選得到（實測拿它當硬性約束會擋掉
    真的要填的欄位）。"""
    lines = []
    for f in FIELDS:
        if skip_derived and f.derived:
            continue
        extra = f" 選項={f.choices}" if f.choices else ""
        hint = f" — {f.hint}" if f.hint else ""
        star = " ★" if mark and f.key in mark else ""
        lines.append(f"- {f.key}: {f.label}{extra}{hint}{star}")
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

# 標籤 → 欄位的確定性對照（squash 後精確比對）。
# 表格印的字和 FIELDS 的 label 一模一樣時，對映沒有第二種答案，
# 不必經過模型；模型第一輪在密集表格裡常整組位移一格，這裡拉回來。
LABEL_ALIASES = {
    "修業期間": "education[].period",
    "在學期間": "education[].period",
    "求學期間": "education[].period",
    "入學年月": "education[].start",
    "畢業年月": "education[].end",
    "任職期間": "experience[].period",
    "工作期間": "experience[].period",
    "服務期間": "experience[].period",
    "固定月薪": "experience[].salary",
    "離職月薪": "experience[].salary",
    "地址": "contact.address_mailing",
    "身份證字號": "basic.national_id",
    "英文名": "basic.name_en",
    "興趣專長": "basic.hobbies",
    "月全薪": "job.expected_salary",
    "日夜": "education[].division",
    "日/夜": "education[].division",
    "畢/肄": "education[].status",
    "科系/所別": "education[].department",
    "就讀學校": "education[].school",
    "服務單位": "experience[].company",
    "服務時間": "experience[].period",
    "出生日期": "basic.birthday",
    "服役資歷": "basic.military",
    "可上班日期": "job.available_date",
    "資訊來源": "job.recruit_channel",
    "手機": "contact.mobile",
    # 「是否有配偶或二親等以內之血親或姻親於本公司任職」——法規寫法，一個「親友」都沒印
    "血親": "declaration.relatives_in_company",
    "姻親": "declaration.relatives_in_company",
    # 學歷表的列首（大學/研究所…）右邊第一格就是學校名稱，
    # 列首挑第幾筆學歷由列指派處理，這裡只負責錨定欄位
    "大學": "education[].school",
    "研究所": "education[].school",
    "高中/專科": "education[].school",
    "專科": "education[].school",
    "高中": "education[].school",
}
# 同名標籤（家庭與諮詢人都有「姓名」之類）對映沒有唯一答案，
# 不能拿來做確定性對齊，留給模型憑上下文判斷。
_label_counts: dict = {}
for _f in FIELDS:
    _label_counts[_f.label] = _label_counts.get(_f.label, 0) + 1
BY_LABEL = {f.label: f.key for f in FIELDS if _label_counts[f.label] == 1}

# 印著這些字的格子一律不填。血型、身高、體重、出生地、身心障礙原本在這裡，
# 2026-08-07 表單需要而補了對應欄位，就必須從這裡拿掉——留著會讓新欄位永遠填不進去。
BLOCKED_LABELS = (
    # 「幾年制」這種個人資料不會有的表單微欄位
    "年制",
    # 每間公司不一樣的值，填了必錯，一律留白讓使用者手寫
    "應徵職務", "應徵職位", "應徵職缺",
    # 應徵的這份工作在哪裡上班（希望工作地點）——跟應徵職務一樣看職缺，個人資料不收
    "工作地點",
)

# 勾選題是拿個人資料的值去比對表單上印出來的選項字串，同義不同字就整格填不進去：
# 表單印「□是 □否」而資料存「無」、印「□退伍」而資料存「役畢」、
# 印「駕照□汽車」而資料存「普通小型車」。
OPTION_SYNONYMS = (
    ("無", "否"),
    ("有", "是"),
    ("未婚", "單身"),
    ("役畢", "退伍"),
    ("普通小型車", "汽車"),
    ("普通重型機車", "機車"),
    ("中華民國", "台灣", "臺灣"),
)

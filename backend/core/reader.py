"""從已填寫的履歷讀出資料。

整份文件交給模型讀，不做任何結構配對——履歷表常用合併儲存格排版，
「標籤在左邊那一格」這種假設在實務上經常不成立。模型看到的是整列，
配對對它是常識。

防幻覺靠驗證：回傳的每個值都必須在原文逐字找得到，找不到就丟掉。
模型只能選取片段，不能編造內容。
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any, Dict, List, Optional

from . import document, llm
from .schema import BY_KEY, FIELDS, describe_fields

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是履歷資料抽取器。使用者會給你一份已經填寫完成的履歷全文，
請把各欄位的值抽出來。

規則：
1. 只能從原文「逐字照抄」，不可改寫、翻譯、換算、補字或推論。抄不到就不要輸出那個鍵。
2. 表格以 | 分隔，同一列中標籤與值通常相鄰。
3. 只抽求職者本人填寫的資料。公司內部欄位（面談情形、任用與否、建議薪資、
   初試複試日期、主管簽名）一律略過。
4. 勾選題輸出被勾選的那一項（■ 或 ☑ 標記的），沒勾就不要輸出。
5. education、experience、family、reference 這類清單可能有多筆，
   依表格由上到下的順序輸出，不要輸出空白列。"""


VISION_RULE = """
6. 另附文件的頁面截圖：用截圖理解表格排版、判斷值屬於哪個欄位；
   但輸出的值一律以「履歷全文」的文字為準逐字照抄，不要抄截圖上看起來的字。"""


# ── 文件的區塊常識:一張表,三個應用 ─────────────────────────────
# 履歷由區塊組成(學歷、工作經驗、自傳…)。_SECTIONS 是唯一事實來源,
# _section_texts() 據此把全文切成 {區塊: 內容},供兩件事用:
#   1. 自傳整段照收(標題下的內容就是值,不需要語意判讀)
#   2. 清單欄位的區塊內驗證(工作經歷的值必須出現在工作經驗區塊裡)
# 第三個應用是 _SECTION_GATES 的 schema 閘門——它刻意用「子字串」比對
# 而非「整行標題」:表格型文件的「推薦人」是儲存格標籤,不會獨立成行,
# 用整行比對會誤關。兩種比對語意不同,所以是兩張表。
_SECTIONS = {
    "autobiography": ("自傳", "自我介紹", "自我推薦"),
    "experience": ("工作經驗", "工作經歷"),
    "education": ("學歷", "教育背景"),
    "certificate": ("資格認證", "證照"),
}
# 不取內容、只當區塊結尾的標題
_BOUNDARY_HEADS = {"專案成就", "作品集", "作品", "語言能力", "求職條件",
                   "專長", "技能", "推薦人", "家庭狀況", "緊急聯絡人", "附件"}


def _section_texts(text: str) -> Dict[str, str]:
    """把原文切成 {區塊: 內容}。標題要獨立成行(squash 後精確比對)才算,
    內文撞名不會誤切;切不出來的區塊就不在結果裡。"""
    heads = {h: root for root, hs in _SECTIONS.items() for h in hs}
    out: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        head = document.squash(line)
        if head in heads:
            current = heads[head]
            out.setdefault(current, [])
        elif head in _BOUNDARY_HEADS:
            current = None
        elif current is not None:
            out[current].append(line)
    return {root: "\n".join(lines).strip() for root, lines in out.items()}


# 這些欄位群是「求職者以外的人」:文件裡沒有對應區塊時,模型會拿求職者
# 自己的姓名電話充當(實測連明文禁令都擋不住)。解法下沉到文法——原文
# 掃不到關鍵字,欄位就不進 schema,受限解碼下模型連生成的機會都沒有。
# 關鍵字保守列舉:漏抽讓人手填,比張冠李戴還預設打勾好。
_SECTION_GATES = {
    "reference": ("推薦人", "諮詢人", "介紹人", "reference"),
    "family": ("家庭狀況", "家庭成員", "家屬", "家人", "父親", "母親"),
    "emergency": ("緊急聯絡", "緊急連絡", "emergency"),
}


def _closed_sections(text: str) -> set:
    low = text.lower()
    return {root for root, keywords in _SECTION_GATES.items()
            if not any(k in low for k in keywords)}


def read(text: str, host: str, model: str,
         images: Optional[List[bytes]] = None) -> Dict[str, Any]:
    """回傳 {欄位代碼: 值}；列表欄位回傳 {root: [{sub: 值}]}。

    images 給 PNG 頁面截圖時走視覺模式：模型同時看到排版與精確文字，
    合併儲存格、標籤與值的歸屬比攤平文字好判斷。逐字驗證照舊，
    看圖看錯的值會因為不在原文中而被丟掉。
    """
    closed = _closed_sections(text)
    if closed:
        log.info("上傳的履歷沒有對應區塊，schema 關閉欄位群：%s", ",".join(sorted(closed)))
    schema = _schema(closed)
    sections = _section_texts(text)   # 全文只切一次,驗證與自傳擷取共用
    # DEBUG 會寫入履歷內容,預設 INFO 不出現——log 可能被附在問題回報裡
    log.debug("read: text_len=%d images=%d closed=%s sections=%s",
              len(text), len(images or []), sorted(closed),
              {r: len(t) for r, t in sections.items()})
    user = f"可抽取的欄位：\n{describe_fields(include_special=False, skip_derived=True)}\n\n履歷全文：\n{text}"
    if images:
        content: List[Dict[str, Any]] = [{"type": "text", "text": user}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        data = llm.ask(host, SYSTEM_PROMPT + VISION_RULE, content, schema,
                       model=model, label=f"讀取履歷(視覺{len(images)}頁)")
    else:
        data = llm.ask(host, SYSTEM_PROMPT, user, schema, model=model, label="讀取履歷")
    log.debug("llm raw output: %r", data)
    kept = _keep_verbatim(data, text, sections)

    # 自傳「標題下面整段照收」:模型要逐字抄上千字幾乎不可能(抄錯一字
    # 就被驗證整段丟棄),所以它一律跳過。程式直接取,逐字正確是天生的
    autobio = sections.get("autobiography", "")
    if not kept.get("autobiography") and len(autobio) >= 30:
        kept["autobiography"] = autobio
        log.info("自傳由程式擷取 %d 字", len(autobio))
    return kept


def _schema(closed: set = frozenset()) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    rows: Dict[str, Dict[str, Any]] = {}

    for f in FIELDS:
        # 合成欄位（就學期間＝入學＋畢業）由起訖兩欄算出來，抽了也進不了表單
        if f.derived:
            continue
        if f.key.split("[].")[0].split(".")[0] in closed:
            continue
        if "[]." in f.key:
            root, sub = f.key.split("[].", 1)
            rows.setdefault(root, {})[sub] = {"type": "string"}
        else:
            props[f.key] = {"type": "string"}

    for root, sub_props in rows.items():
        props[root] = {"type": "array",
                       "items": {"type": "object", "properties": sub_props}}
    return {"type": "object", "properties": props}


# 日期欄位的值只能由日期會用到的字元組成。跟前端 DateSelect.parseDate 的
# 同名判準必須一致——民國年、2016/9 都要放行，「28歲」「2年10個月」要擋掉。
DATE_ONLY_RE = re.compile(r"^[\d\s年月日民國/.-]+$")


def _drop_reason(key: str, value: str, hay: str, whole: str) -> Optional[str]:
    """值該不該丟?回傳丟棄原因代碼,None＝通過。

    值必須逐字出現在驗證範圍內；日期欄位還要真的長得像日期——模型很愛
    把年齡當生日（104 履歷只印「28歲」），那個值確實出現在原文，
    光靠逐字驗證擋不住，會直接蓋掉使用者原本填好的生日。
    """
    sq = document.squash(value)
    if sq not in hay:
        return "not_in_section" if (hay is not whole and sq in whole) else "not_in_source"
    spec = BY_KEY.get(key)
    if spec and spec.kind == "date" and not DATE_ONLY_RE.match(value.strip()):
        return "not_a_date"
    return None


def _keep_verbatim(data: Dict[str, Any], source: str,
                   sections: Dict[str, str]) -> Dict[str, Any]:
    haystack = document.squash(source)
    # 清單欄位群切得出區塊時,驗證範圍縮小到自己的區塊——「待業中」(就業
    # 狀態)才不會被充當成離職原因。切不出就退回全文,不會比較嚴
    scoped = {root: document.squash(t) for root, t in sections.items() if t}
    kept: Dict[str, Any] = {}
    dropped: List[str] = []

    for key, value in data.items():
        if isinstance(value, list):
            hay = scoped.get(key, haystack)
            scope = key if key in scoped else "whole"
            rows = []
            for i, row in enumerate(value):
                if not isinstance(row, dict):
                    continue
                clean: Dict[str, str] = {}
                for k, v in row.items():
                    if not (isinstance(v, str) and v.strip()):
                        continue
                    fkey = f"{key}[].{k}"
                    reason = _drop_reason(fkey, v, hay, haystack)
                    log.debug("verify %s#%d value=%r scope=%s(len=%d) -> %s",
                              fkey, i, v[:40], scope, len(hay), reason or "keep")
                    if reason:
                        dropped.append(fkey)
                    else:
                        clean[k] = v.strip()
                if clean:
                    rows.append(clean)
            if rows:
                kept[key] = rows
        elif isinstance(value, str) and value.strip():
            reason = _drop_reason(key, value, haystack, haystack)
            log.debug("verify %s value=%r scope=whole(len=%d) -> %s",
                      key, value[:40], len(haystack), reason or "keep")
            if reason:
                dropped.append(key)
            else:
                kept[key] = value.strip()

    if dropped:
        # 只記欄位代碼——被丟掉的多半是模型改寫過的個資
        log.warning("捨棄非原文的抽取結果 欄位=%s", ",".join(sorted(set(dropped))))
    return kept

# -*- coding: utf-8 -*-
r"""空白履歷表 ＋ 個人資料（app.db 那份 JSON）→ 填好的履歷表。

視覺版的填寫引擎：讓模型看著版面示意圖決定「每一個位置放哪一項資料」。
另一條路是 planner.py（規則錨定＋純文字模型），兩條都在，由 service 決定用哪一條。

這個檔案同時是研究對象：claude_code_in_agent/ 那套評分與研究迴圈跑的就是它，
改進填寫效果就是改這裡。介面只有三個：analyze()、write()、solve()。

作法：程式決定「哪裡能寫、怎麼寫」，模型只決定「這個位置放哪一項資料」。

  1. 照 docx 的表格骨架自己畫版面示意圖，每格標上地址 —— 讓 VLM 看得到位置
  2. 機械地列出所有「可以寫字的位置」：空格子、印好的字之間的留白、冒號後面、
     勾選框。「以下由公司填寫」之後、應徵職務這種每間公司不同的欄位不列
  3. 一列一筆的表（學經歷、家人）整張問模型「每一欄是什麼」，列的順序由程式排
  4. 其餘位置分批問模型：這裡該填哪一項資料（挑不到就 __無__）。答案被 JSON Schema
     約束成只能挑既有的項目代碼，模型編不出不存在的資料
  5. 補漏：還空著的位置，只拿名稱相近、還沒用到的幾項資料再問一次
  6. 過濾與去重：放錯地方的配對擋掉（詞對不上、表格沒提到的別人的資料、選項值
     離開了題目），同一項資料在同一張表只留欄名最像的那一格
  7. 字面對不上的勾選題（資料「無」、表格印「□否」）交給模型判斷意思；
     是非題只讓模型判斷相不相關，勾哪個由程式決定
  8. 程式把值寫進去：日期照單位拆格、西元換民國、期間由起訖重算。
     原本印在表格上的字一個都不會動

第一版是讓模型輸出「整格的新內容」，三種錯誤同時發生：值被寫進標題格、
憑空捏造沒有的資料、順手改掉原本印的標點（40 格只對 13 格，另外多填 68 格）。
位置與寫法收回程式手上之後，這三類錯誤從結構上消失。

實測結果與每一項修正的效果見同目錄的 notes.md。

單獨試跑一份與逐格評分：claude_code_in_agent/README.md
"""
from __future__ import annotations

import base64
import copy
import io
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import groupby, permutations
from pathlib import Path
from typing import Any, Dict, List, Tuple

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from PIL import Image, ImageDraw, ImageFont

from . import llm
from .document import iter_block_items
from .schema import BLOCKED_LABELS, BY_KEY, LABEL_ALIASES

log = logging.getLogger(__name__)

# 預設值只給研究用的入口；產品一律由 service 把 config 裡的設定傳進來
LLM_HOST = "http://localhost:8085"
LLM_MODEL = "Qwen3.5-9B-Q4_K_M"

# 版面示意圖：自己畫，不借助任何外部排版引擎（LibreOffice、Word 都不能用——
# 產品是可攜、離線的單機工具，裝不進去的東西就不能進解法）
FONT_PATH = os.environ.get("FORM_FONT", r"C:\Windows\Fonts\msjh.ttc")
PAGE_W, PAGE_H, MAX_PAGES = 1100, 1500, 4
FONT_SIZE, ADDR_SIZE, LINE_H, ADDR_H = 15, 11, 20, 14

BATCH = 16     # 一次問幾個位置。問多了模型會整批放棄（見 backend/core/planner.py 的實測）
BOX_BATCH = 8  # 一次問幾題勾選題。二十題一起問，宣告事項那七題會整批答不出來
NONE = "__無__"

CHECKBOX_CHARS = "□☐▢◻"
CHECKED = "■"
# 印好的字之間留出來的書寫空間：「自　　年　　月」「血型：　　型」。
# 兩格以上的空白才算，全形半形混著數；剛好兩格的、以及行首的，要後面接著單位才算
# ——「年  月  日」的月日前面只有兩格，而「cm  體重」中間那兩格只是排版間隔，
# 「　　　　初試日期：」開頭那一長串則是縮排。
GAP_RE = re.compile(r"[ 　]{2,}|[_＿]{2,}|[.．]{4,}")
# 印在空格後面的單位。值一律寫在單位前面（「＿＿年」「＿＿公分」），認得單位有兩個
# 用處：判斷一段空白是不是留白，以及日期要拆成哪幾格
UNITS = ("公分", "公斤", "cm", "kg", "年", "月", "日", "時", "分", "秒", "歲", "型", "元")
DATE_UNITS = ("年", "月", "日", "時", "分", "秒")
DATE_RE = re.compile(r"(\d{2,4})\s*[年/.\-]\s*(\d{1,2})(?:\s*[月/.\-]\s*(\d{1,2}))?")
PLACEHOLDER_RE = re.compile(r"^[\s　_＿…．.\-—–]*$")
# 多選值的分隔符號。刻意不含空白：「2026 年 8 月 24日」用空白拆會拆出一個「月」，
# 剛好跟可到職日的選項「□　月　日」字面相同，就被當成已經勾好、日期不寫了
OPTION_SPLIT_RE = re.compile(r"[、，,／/；;]+")
# 「※以下欄位由本公司人員填寫※」——表格自己說了後面不是求職者填的
COMPANY_ONLY_RE = re.compile(r"以下.{0,8}(公司|人事|人資).{0,8}填")
# 別人的資料：表格上沒印這些字，就不拿出來給模型挑（同產品匯入端 reader._SECTION_GATES）
OTHER_PEOPLE = {
    "emergency": ("緊急聯絡", "緊急連絡"),
    "family": ("家庭", "家屬", "家人", "父", "母"),
    "reference": ("推薦", "諮詢", "介紹人"),
}
# 公司自己填的欄位。「以下由公司填寫」那條線之前也會夾雜這種欄位（面談日期印在最上面）
COMPANY_WORDS = ("面談", "初試", "複試", "任用", "建議薪資", "主管簽章", "到職日期")
# 兩邊都常出現、卻不代表相關的詞：履歷表的問答題幾乎都有「工作」；
# 「期間」則是服役期間、就學期間、任職期間都有，光靠它會把學歷填進服役欄
GENERIC_WORDS = {"工作", "期間"}

SYSTEM = """你是履歷表格的填寫助理。表格上每一個「可以寫字的位置」都編了號，
位置在那一格的字裡用 ▁ 標出來。請為每一個位置指出：該填個人資料裡的哪一項。

規則：
1. 只能從個人資料的項目代碼裡挑，挑不到就填 __無__。值長什麼樣不用你管，
   程式會照個人資料原樣填進去。
2. 這些位置一律填 __無__：公司自己要填的（初試日期、複試日期、面談情形、
   任用與否、建議薪資、主管簽章）、個人資料裡沒有那項的、以及只是排版留白的地方。
3. ▁ 原本是 □ 的位置是勾選框：填「這個選項在問哪一項資料」就好。勾哪一個由程式
   比對資料的值跟選項決定，意思相同也算（資料「無」對選項「否」）。
   例如「□未婚 □已婚」兩個位置都填 basic.marital_status 是對的。
4. 多筆的資料（學歷、工作經歷、家人）看上面欄名後面標的「往下第幾列」：
   第 1 列填 [0]、第 2 列填 [1]，依此類推；資料沒有那一筆就填 __無__。
5. 表格問的若是別人的資料（家庭成員、緊急聯絡人、推薦人），個人資料裡沒有那個人，
   就填 __無__，不要拿本人的姓名生日頂替。
6. 上面兩條是為了不要亂填，但也不要因為不確定就一律填 __無__：
   左邊欄名或上面欄名對得上個人資料裡的某一項，那就是它，填下去。
7. 每一個位置都要回答一次。"""


@dataclass
class Cell:
    """文件裡一個可以寫字的容器（表格的一格，或內文的一段）。"""
    addr: str
    paras: List[Any]
    row_head: str = ""      # 左邊最近一格印的字（欄名）
    col_head: str = ""      # 同一欄上面印的字
    depth: int = 0          # 空格子在上面欄名底下第幾列（一列一筆的表才有）
    printed_in_row: int = 0    # 這一列有幾格印著欄位名稱（欄名列不會只有一兩格）
    value_after: bool = False  # 右邊還有空格子（代表這格是欄名，不是提示字）
    caption: str = ""          # 欄名列正上方那一句說明（「請列舉…並同意我們諮詢」）


@dataclass
class Slot:
    """一個可以寫字的位置。程式知道怎麼寫，模型只要說「這裡放哪一項資料」。"""
    id: str
    kind: str               # blank | gap | append | line | box
    addr: str
    para: int               # 在 Cell.paras 的第幾段
    start: int
    end: int
    filler: str = ""        # gap 原本的留白，用來保持欄寬
    option: str = ""        # box：選項印的字；gap：緊接在後面的單位（年、月、kg），有單位只收數字
    preview: str = ""       # 給模型看的那一格內容，目標位置標成 ▁
    cell: Cell = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# 一、把文件拆成「格子」與「可以寫字的位置」
# ---------------------------------------------------------------------------

def _unit_at(text: str, pos: int) -> str:
    """text 從 pos 開始印的是哪一個單位，沒有就回空字串。

    後面還接著中英文就不算，不然「每月薪資」的「月」也會被當成單位。
    """
    rest = text[pos:]
    for unit in sorted(UNITS, key=len, reverse=True):
        if rest.startswith(unit) and not re.match(r"[一-鿿A-Za-z]", rest[len(unit):]):
            return unit
    return ""


_TEMPLATE_RE = re.compile(r"[\s　\d自至到~～\-—/／()（）]|" + "|".join(sorted(UNITS, key=len, reverse=True)))


def _is_template(text: str) -> bool:
    """整格只印著格式，不是欄位名稱也不是值：「自　　年　　月／至　　年　　月」、
    「　　公分／　　公斤」。這種格子跟空格子一樣是拿來填的。"""
    return bool(text.strip()) and not _TEMPLATE_RE.sub("", text).strip()


def _box_only(text: str) -> bool:
    """整格只有勾選框跟很短的選項字：「□畢 □肄」。一列一筆的表也會整欄都是這種格子。"""
    body = re.sub(f"[{CHECKBOX_CHARS}{CHECKED}]", "", text)
    return (any(c in text for c in CHECKBOX_CHARS + CHECKED)
            and len(_squash(body)) <= 6 and not re.search(r"[：:]", text))


def _merged_away(tc) -> bool:
    """這一格是垂直合併的延續格——字都在最上面那格裡，寫進來看不見。"""
    pr = tc.tcPr
    vmerge = None if pr is None else pr.find(qn("w:vMerge"))
    return vmerge is not None and vmerge.get(qn("w:val"), "continue") == "continue"


def cells(doc) -> List[Cell]:
    """列出所有格子。地址與 evaluate.py 的報告一致，對錯照著地址查得到。

    直接數 XML 裡的 tc（這一列實際存在的儲存格），不用 row.cells——合併儲存格
    在 row.cells 會重複出現，而 lxml 元素代理是隨用隨建的，拿 id() 去重會時多時少。
    順便記下同列左邊、同欄上面印的字：判斷「這一格是什麼」全靠它們。
    """
    out: List[Cell] = []
    table_no = para_no = 0
    for block in iter_block_items(doc):
        if isinstance(block, Table):
            above: Dict[int, str] = {}          # 網格欄 -> 上面最近一格的欄名
            above_row: Dict[int, int] = {}      # 網格欄 -> 那個欄名在第幾列
            cap_row, cap_text = -2, ""          # 最近一列「整列只有一句長說明」的列
            for r, row in enumerate(block.rows):
                grid_col, in_row = 0, []
                for c, tc in enumerate(row._tr.tc_lst):
                    col = grid_col
                    grid_col += tc.grid_span or 1
                    if _merged_away(tc):
                        # 延續格不是填寫位置，但上面那格的字仍是它右邊格子的欄名：
                        # 「聯絡電話」跨兩列，第二列的「(H)：」左邊就是它
                        in_row.append((col, None, None, above.get(col, "")))
                        continue
                    paras = _Cell(tc, block).paragraphs
                    text = "\n".join(p.text for p in paras).strip()
                    in_row.append((col, f"t{table_no}.r{r}.c{c}", paras, text))

                def is_label(t):
                    return bool(t) and not _is_template(t) and not _box_only(t)

                labels = [t for _c, a, _p, t in in_row if a and is_label(t)]
                # 整列只有一句長說明：那是這一區在講什麼。諮詢人那張表沒有左邊的
                # 區塊標題，欄名又只有「姓名／職稱／公司名稱」，跟家庭成員長得一樣，
                # 只有上面那句「請列舉…並同意我們諮詢」認得出它問的是誰
                if len(labels) == 1 and len(_squash(labels[0])) > 8:
                    cap_row, cap_text = r, labels[0]
                for i, (col, addr, paras, text) in enumerate(in_row):
                    if addr is None:
                        continue
                    # 左邊最近一格印的字才是這一格的欄位名稱。取「整列第一格」會
                    # 把「性別｜　｜血型｜　」的血型欄也標成性別——履歷表一列排
                    # 兩三組「標籤＋值」是常態，錯的標籤比沒有標籤還糟
                    left = next((t for _c, _a, _p, t in reversed(in_row[:i]) if t), "")
                    # 空著的、只印格式的（「自　年　月」）、整格勾選框的（「□畢 □肄」）
                    # 都是拿來填的。左邊整排都沒有欄位名稱時，標出它在欄名底下第幾列
                    # ——模型靠這個分辨該填第幾筆，沒有它會整批錯位一列
                    alone = (not is_label(text)
                             and not any(a and is_label(t) for _c, a, _p, t in in_row[:i]))
                    depth = r - above_row[col] if alone and col in above_row else 0
                    # 說明要緊貼著欄名列才算數，隔了幾列的就不是在講這一區
                    caption = cap_text if depth and cap_row == r - depth - 1 else ""
                    out.append(Cell(addr, paras, row_head=left, depth=depth,
                                    col_head="" if is_label(text) else above.get(col, ""),
                                    printed_in_row=len(labels), caption=caption,
                                    value_after=any(a and not t
                                                    for _c, a, _p, t in in_row[i + 1:])))
                    if is_label(text):
                        above[col], above_row[col] = text, r
            table_no += 1
        else:
            if COMPANY_ONLY_RE.search(block.text):
                break           # 這一段之後都是公司自己的欄位（面談情形、任用與否、建議薪資）
            out.append(Cell(f"p{para_no}", [block]))
            para_no += 1
    return out


def _line_slots(line: str) -> List[Tuple[str, int, int, str]]:
    """一行字裡有哪些可以寫字的位置：(kind, start, end, 選項字或單位)。

    有勾選框的行，只有最後一個框之後的留白算數——框與框之間的空白是排版
    （「□隨時  □   週」），填進去只會把版面弄壞；框後面的才是真的要寫字
    （「□   月   日」要寫月份與日期）。
    """
    if not line.strip():
        return []
    if any(b in _squash(line) for b in BLOCKED_LABELS + COMPANY_WORDS):
        return []    # 應徵職務（每間公司不一樣，產品刻意留白）與公司自己填的欄位
    out: List[Tuple[str, int, int, str]] = []
    boxes = [m.start() for m in re.finditer(f"[{CHECKBOX_CHARS}]", line)]
    for i, pos in enumerate(boxes):
        stop = boxes[i + 1] if i + 1 < len(boxes) else len(line)
        option = re.split(r"[\s　,，、/／]+", line[pos + 1:stop].strip())[0].strip(" :：()（）")
        out.append(("box", pos, pos + 1, option))
    # 「學  歷」「姓    名」中間的空白是把字撐開的排版，不是留給人寫字的。
    # 拿掉空白後只剩三兩個字、又沒有冒號、數字或單位的，整段就是個欄位名稱
    padded = (len(_squash(line)) <= 4 and not re.search(r"[：:\d]", line)
              and not any(u in line for u in UNITS))
    if not padded:
        for m in GAP_RE.finditer(line, boxes[-1] + 1 if boxes else 0):
            unit = _unit_at(line, m.end() + len(line[m.end():]) - len(line[m.end():].lstrip(" 　")))
            filler = m.group()
            if len(filler) < 3 and not unit and filler[0] not in "_＿.．":
                continue    # 兩格寬的空白要後面接著單位才算（「cm  體重」中間那兩格不算）
            if not line[:m.start()].strip() and not unit:
                continue    # 行首的空白後面接欄位名稱就只是縮排（「　　　初試日期：」）
            out.append(("gap", m.start(), m.end(), unit))
    # 冒號結尾＝值接在後面；「語言:1.」這種編號結尾也是（1. 是清單序號，值寫在後面）。
    # 要看未經 strip 的原字：「中文：      」的留白已經被上面認成 gap 了，
    # 再補一個 append 就會有兩個位置搶同一個地方
    if line.endswith(("：", ":")) or re.search(r"\d[.．、)）]$", line):
        out.append(("append", len(line), len(line), ""))
    elif not out and line.rstrip().endswith(("？", "?")):
        out.append(("line", len(line), len(line), ""))   # 整格是一個問句，答案寫下一行
    return out


def _window(text: str, at: int, span: int = 34) -> str:
    """▁ 前後各取一段。長的聲明文字只截開頭的話，▁ 附近印了什麼根本看不到。"""
    lo, hi = max(0, at - span), at + span + 1
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def _fill_after(cell: Cell, text: str) -> bool:
    """這一段印的字後面就是要寫字的地方嗎？

    兩種：整段被括號包起來的寫法說明（戶籍地址欄印著「(請註明里、鄰)」，人就寫在那格），
    以及左邊有欄位名稱、右邊又沒有空格子的短提示（通訊地址欄印著「同戶籍地址」）。
    欄名列不算——一列印著五六個欄位名稱那是表頭，不是提示。
    """
    body = text.strip()
    if re.fullmatch(r"[（(][^（()）]*[)）]", body):
        return True
    return (bool(cell.row_head) and not cell.value_after and cell.printed_in_row <= 2
            and 0 < len(_squash(body)) <= 8 and not re.search(r"[：:？?]", body))


def slots_of(cell: Cell) -> List[Slot]:
    """一個格子裡所有可以寫字的位置。"""
    out: List[Slot] = []
    full, offset = "\n".join(p.text for p in cell.paras), 0
    # 聲明條文那種長段落之間的空行是排版，不是填寫位置
    legal = any(len(p.text) > 60 and p.text.rstrip().endswith("。") for p in cell.paras)
    for pi, para in enumerate(cell.paras):
        text = para.text
        if PLACEHOLDER_RE.match(text):
            found = [] if legal else [("blank", 0, len(text), "")]
        else:
            found, base = [], 0
            for line in text.split("\n"):
                found += [(k, s + base, e + base, opt)
                          for k, s, e, opt in _line_slots(line)]
                base += len(line) + 1
            if not found and _fill_after(cell, text):
                found = [("append", len(text), len(text), "")]
        for n, (kind, start, end, option) in enumerate(found, 1):
            if kind == "blank" and len(cell.paras) > 1:
                # 格子裡的空白段本身沒有字，要把整格攤開才看得出它夾在什麼中間
                shown = full[:offset] + "▁" + full[offset + len(text):]
            else:
                shown = text[:start] + "▁" + text[end:]
            # 排版用的留白先縮成一格：宣告事項那六題的題目與勾選框之間空了五十格，
            # 不縮的話周圍只看得到「▁ 是,請說明:」，題目在問什麼完全看不見
            shown = re.sub(r"[ 　]{2,}", " ", shown.replace("\n", "↵"))
            out.append(Slot(f"{cell.addr}#{pi}.{n}", kind, cell.addr, pi, start, end,
                            filler=text[start:end] if kind == "gap" else "",
                            option=option, preview=_window(shown, shown.index("▁")),
                            cell=cell))
        offset += len(text) + 1
    # 格子裡印了「優點：」「缺點：」這種更明確的位置時，問句後面就不算一個位置了——
    # 不然模型會把答案寫在問句後面，印好的欄位反而空著
    if any(s.kind == "append" for s in out):
        out = [s for s in out if s.kind != "line"]
    return out


# ---------------------------------------------------------------------------
# 二、個人資料攤平成「項目代碼 → 值」
# ---------------------------------------------------------------------------

def flatten(profile: Any, prefix: str = "") -> Dict[str, str]:
    out: Dict[str, str] = {}
    if isinstance(profile, dict):
        for key, value in profile.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(profile, list):
        for i, value in enumerate(profile):
            out.update(flatten(value, f"{prefix}[{i}]"))
    elif str(profile).strip():
        out[prefix] = str(profile).strip()
    return out


def _spec(path: str):
    """education[0].school → schema 裡 education[].school 的定義。"""
    return BY_KEY.get(re.sub(r"\[\d+\]", "[]", path))


def _label(path: str) -> str:
    spec = _spec(path)
    return f"（{spec.label}）" if spec else ""


def _kind(path: str) -> str:
    spec = _spec(path)
    return spec.kind if spec else ""


# 整個值就是一個日期：2016/9、2013年09月、2023 年 7 月、1998年03月25日 都算
_DATE_ONLY_RE = re.compile(
    r"^\s*(\d{2,4})\s*[年/.\-]\s*(\d{1,2})\s*[月/.\-]?\s*(?:(\d{1,2})\s*日?)?\s*$")


def _canon_date(value: str) -> str:
    """日期一律寫成「2016年9月」。

    使用者存進來的寫法從來沒一致過——同一份 app.db 裡就有 2016/9、2013年09月、
    2023 年 7 月三種。原樣照抄的話，學歷表上下兩列會長得不一樣。

    印著「＿年＿月」的格子不受影響：那種格子是照印好的單位一格一格填，年月由
    表格提供，這裡補上的年月反而會被格子的單位重複。認不出是日期就原樣保留，
    不替使用者猜——「民國87年」「2020」這種留給它原本的樣子。
    """
    m = _DATE_ONLY_RE.match(value or "")
    if not m:
        return value
    year, month, day = m.groups()
    return (f"{int(year)}年{int(month)}月"
            + (f"{int(day)}日" if day else ""))


def fields_of(profile: Dict[str, Any]) -> Dict[str, str]:
    """產品的個人資料（app.db 那份 JSON）攤平成「項目代碼 → 值」。

    日期一律換成同一種寫法（見 _canon_date）：存的是使用者自己打的，填出去的
    要是一致的，換寫法是填寫這一端的事。

    schema 標成合成的欄位（就學期間、任職期間）不採用存著的值，一律由起訖重算——
    app.db 裡就有一筆存成「自 2023 年 2 月 至 2021 年 10 月」，起訖顛倒。
    """
    out = {k: (_canon_date(v) if _kind(k) == "date" else v)
           for k, v in flatten(profile).items()
           if not getattr(_spec(k), "derived", False)}
    for root in ("education", "experience"):
        for i, row in enumerate(profile.get(root) or []):
            if isinstance(row, dict) and row.get("start") and row.get("end"):
                out[f"{root}[{i}].period"] = (f"{_canon_date(row['start'])}"
                                              f"~{_canon_date(row['end'])}")
    return out


# ---------------------------------------------------------------------------
# 三、版面示意圖
# ---------------------------------------------------------------------------

def _wrap(draw, text: str, font, width: float) -> List[str]:
    """照像素寬度斷行。中文沒有空白可以斷，只能逐字量。"""
    out: List[str] = []
    for raw in text.split("\n"):
        line = ""
        for ch in raw:
            if line and draw.textlength(line + ch, font=font) > width:
                out.append(line)
                line = ch
            else:
                line += ch
        out.append(line)
    return out or [""]


def _layout(doc, draw, font) -> tuple:
    """算出每一格畫在哪裡。回傳 ([(方框, 地址, 文字行)], 總高度)。

    欄寬照 docx 自己的 tblGrid（表格真正的欄位寬度），橫向合併用 gridSpan 併欄，
    所以畫出來的相對位置與寬窄跟原檔一致。
    """
    items, y = [], 10
    table_no = para_no = 0
    for block in iter_block_items(doc):
        if isinstance(block, Table):
            cols = [int(g.w or 0) for g in block._tbl.tblGrid.gridCol_lst] or [1]
            edges, acc = [0.0], 0
            for w in cols:
                acc += w
                edges.append(acc)
            xs = [10 + (PAGE_W - 20) * e / (acc or 1) for e in edges]
            for r, row in enumerate(block.rows):
                grid_col, in_row = 0, []
                for c, tc in enumerate(row._tr.tc_lst):
                    x0 = xs[min(grid_col, len(xs) - 1)]
                    grid_col += tc.grid_span or 1
                    x1 = xs[min(grid_col, len(xs) - 1)]
                    text = "\n".join(p.text for p in _Cell(tc, block).paragraphs).strip()
                    in_row.append((x0, x1, f"t{table_no}.r{r}.c{c}",
                                   _wrap(draw, text, font, max(x1 - x0 - 10, 20))))
                height = max(len(lines) for *_, lines in in_row) * LINE_H + ADDR_H + 6
                items += [((x0, y, x1, y + height), addr, lines)
                          for x0, x1, addr, lines in in_row]
                y += height
            table_no += 1
            y += 8
        else:
            if block.text.strip():
                lines = _wrap(draw, block.text.strip(), font, PAGE_W - 30)
                height = len(lines) * LINE_H + ADDR_H + 6
                items.append(((10, y, PAGE_W - 10, y + height), f"p{para_no}", lines))
                y += height
            para_no += 1
    return items, y + 10


def render_pages(doc) -> List[bytes]:
    """把版面畫成示意圖，一張圖一頁。

    不做像素級還原（那需要排版引擎，而 LibreOffice、Word 都不能用——產品是可攜
    離線的單機工具）。模型需要的本來也不是字型，而是「哪一格在哪、多寬、跟誰同一列」，
    這些從 docx 的表格骨架就畫得出來。

    每一格的地址直接印在格子左上角：模型看得到位置，才對得回它要回答的地址。
    """
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    small = ImageFont.truetype(FONT_PATH, ADDR_SIZE)
    items, height = _layout(doc, ImageDraw.Draw(Image.new("RGB", (1, 1))), font)

    canvas = Image.new("RGB", (PAGE_W, height), "white")
    draw = ImageDraw.Draw(canvas)
    for (x0, y0, x1, y1), addr, lines in items:
        draw.rectangle((x0, y0, x1, y1), outline=(150, 150, 150))
        draw.text((x0 + 4, y0 + 1), addr, font=small, fill=(190, 60, 60))
        for i, line in enumerate(lines):
            draw.text((x0 + 5, y0 + ADDR_H + i * LINE_H), line, font=font, fill=(0, 0, 0))

    if height > MAX_PAGES * PAGE_H:      # 太長的表格整張縮小，不然圖多到塞爆上下文
        canvas = canvas.resize((PAGE_W, MAX_PAGES * PAGE_H))
        height = MAX_PAGES * PAGE_H

    out = []
    for top in range(0, height, PAGE_H):
        buf = io.BytesIO()
        canvas.crop((0, top, canvas.width, min(top + PAGE_H, height))).save(buf, "PNG")
        out.append(buf.getvalue())
    return out


# ---------------------------------------------------------------------------
# 四、問模型：這個位置放哪一項資料
# ---------------------------------------------------------------------------

def _describe(slot: Slot) -> str:
    cell, parts = slot.cell, []
    if cell.row_head:
        parts.append(f"左邊欄名:{_squash(cell.row_head)[:14]}")
    if cell.col_head:
        depth = f"（往下第{cell.depth}列）" if cell.depth else ""
        parts.append(f"上面欄名:{_squash(cell.col_head)[:14]}{depth}")
    kind = f"（勾選框，選項印著「{slot.option}」）" if slot.kind == "box" else ""
    return f"{slot.preview}{kind}" + "".join(f"｜{x}" for x in parts)


def ask(batch: List[Slot], fields: Dict[str, str], images: List[bytes],
        used: Dict[str, str], host: str = LLM_HOST, model: str = LLM_MODEL) -> Dict[str, str]:
    """問模型這一批位置各自要填哪一項資料。回傳 {位置編號: 項目代碼}。"""
    # 位置編號當成 JSON 的鍵：文法會自己把編號一個一個吐出來，模型只填「哪一項資料」。
    # 之前讓模型自己配 {slot, field} 一對一對地寫，它會整批位移一格
    # （優缺點那格把「優點」寫進問句行、「缺點」寫進優點欄）。鍵由文法產生就錯不了。
    # 鍵用 s1、s2 這種短名，不用完整地址——地址一個 12 個字，16 個位置光是把鍵
    # 逐字吐出來就佔掉大半輸出，模型剩不了多少心力在「挑哪一項」上
    ids = [f"s{i}" for i in range(1, len(batch) + 1)]
    choices = {"type": "string", "enum": list(fields) + [NONE]}
    schema = {
        "type": "object",
        "properties": {sid: choices for sid in ids},
        "required": ids,
        "additionalProperties": False,
    }
    # 每一批只有最後一句不同：個人資料與示意圖都排在前面，llama-server 的提示快取
    # 才吃得到。不再重貼整份位置清單——整張表格的長相示意圖已經畫給模型看了，
    # 再貼一份 180 行的清單只是讓每一批的提示多五千個 token
    user: List[Dict[str, Any]] = [{"type": "text", "text": (
        "個人資料（項目代碼：值）：\n"
        + "\n".join(f"- {k}{_label(k)}：{v[:60]}" for k, v in fields.items()))}]
    for img in images:
        user.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")}})
    # 已經用掉的資料排在最後（放前面會把提示快取的共同前綴打斷）。沒有這一段的話，
    # 模型看不到別批的決定：五個問答題配七項回答，每一批都從頭挑一次就會互相搶
    done = ("\n\n已經填在別的位置的資料項（不要再挑，除非這一格真的也要填同一項）：\n"
            + "、".join(sorted(set(used.values()))) if used else "")
    user.append({"type": "text", "text": "這一批要判斷的位置：\n" + "\n".join(
        f"  {sid}｜{_describe(s)}" for sid, s in zip(ids, batch)) + done})

    data = llm.ask(host, SYSTEM, user, schema, model=model,
                   label=f"配對:{len(ids)}處")
    back = dict(zip(ids, batch))
    return {back[sid].id: key for sid, key in data.items()
            if sid in back and key in fields}


# ---------------------------------------------------------------------------
# 五、把值寫進去。原本印的字一個都不動
# ---------------------------------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"[\s　]+", "", text or "")


def _ticked(option: str, value: str) -> bool:
    """這個選項該不該勾：選項印的字要跟值（或值裡用頓號分開的其中一項）完全相同。

    刻意不做包含比對——「同意」是「不同意」的子字串，包含比對會兩個都勾起來。
    反過來「Windows、Word、Excel」拆開後每一項都對得上，多選也照樣成立。
    """
    if not option:
        return False
    cands = {_squash(value)} | {_squash(p) for p in OPTION_SPLIT_RE.split(value) if p}
    return _squash(option) in cands


def _marker_after(text: str, end: int) -> str:
    """空格後面緊接著印的單位：「民國　▁　年」的 ▁ 後面是「年」。"""
    return _unit_at(text[end:].lstrip(" 　"), 0)


def _split_by_markers(value: str, markers: List[str]) -> List[str]:
    """把一個值照單位切開：「1998年03月25日」＋［年,月,日］→［1998, 3, 25］。

    每一段取單位前面最後一組數字：「2026 年 8 月 24日」只照［月,日］切時，
    月那一段是「2026 年 8」，要的是 8——表格沒印年，年份就不寫。
    前導零拿掉，人寫「3 月」不寫「03 月」。

    切完必須剛好用完整個值，剩下尾巴就算失敗——「1998年03月25日」只照［年,月］
    切得出 1998、3，但 25 日沒地方去，那就不是這個值該去的地方。
    """
    parts, cursor = [], 0
    for marker in markers:
        idx = value.find(marker, cursor)
        if idx < 0:
            return []
        nums = re.findall(r"\d+", value[cursor:idx])
        parts.append((nums[-1].lstrip("0") or "0") if nums else value[cursor:idx].strip())
        cursor = idx + len(marker)
    return parts if all(parts) and not value[cursor:].strip() else []


def _pad(slot: Slot, value: str) -> str:
    """值比原本的留白短就補回空白，欄寬不會跑掉。"""
    return value + (slot.filler[len(value):] if slot.filler.isspace() else "")


def _replacement(slot: Slot, value: str) -> str:
    if slot.kind == "box":
        return CHECKED if _ticked(slot.option, value) else ""
    if slot.kind == "line":
        return "\n" + value
    if slot.kind == "gap":
        return _pad(slot, value)
    return value


def _set_para(para, text: str) -> None:
    """整段換成新內容，沿用原本第一個 run 的字型。
    python-docx 會把換行字元轉成 <w:br/>，看起來一樣是換行。"""
    if para.runs:
        para.runs[0].text = text
        for extra in para.runs[1:]:
            extra.text = ""
    elif text:
        para.add_run(text)


def _set_para_marked(para, parts: List[Tuple[str, bool]]) -> None:
    """跟 _set_para 寫的字一樣，但把填進去的那幾段標成黃底。

    只給網頁上的左右對照用——要一眼看出值落在哪一格。下載的成品走 _set_para，
    一個底色都不加：黃底留在成品裡，使用者還得自己去 Word 清掉。
    """
    template = para.runs[0]._element.find(qn("w:rPr")) if para.runs else None
    for run in list(para.runs):
        run._element.getparent().remove(run._element)
    for text, added in parts:
        if not text:
            continue
        run = para.add_run(text)
        if template is not None:
            run._element.insert(0, copy.deepcopy(template))
        if added:
            props = run._element.get_or_add_rPr()
            props.append(props.makeelement(qn("w:highlight"), {qn("w:val"): "yellow"}))


def _roc(text: str, slot: Slot, part: str, marker: str) -> str:
    """表格印「民國＿＿年」而資料存西元年時，換成民國年。資料存西元是對的——
    表格有的印民國、有的印西元，換算是填寫這一端的事。"""
    if (marker == "年" and part.isdigit() and int(part) > 1911
            and re.search(r"民國\s*\Z", text[:slot.start])):
        return str(int(part) - 1911)
    return part


def _date_parts(value: str, markers: List[str]) -> List[str]:
    """把日期填進「＿年＿月＿日」這種格子。格子問的全是日期單位時才動手。

    「2016/9」「2013年09月」「自 2021 年 10 月 至 2023 年 2 月」寫法各不相同，照字面
    切是切不開的（值裡根本沒有「年」）。這裡先把值裡的日期解析出來，再照格子問的
    單位一格一格給；遇到第二個「年」就換下一個日期——「自＿年＿月 至＿年＿月」
    要的是起訖兩個。
    """
    if not markers or any(m not in DATE_UNITS for m in markers):
        return []
    dates = [m.groups() for m in DATE_RE.finditer(value)]
    if not dates:
        return []
    out, i, seen = [], 0, set()
    for marker in markers:
        if marker in seen:
            i, seen = i + 1, set()
        if i >= len(dates):
            return []
        part = dict(zip(("年", "月", "日"), dates[i])).get(marker)
        if not part:
            return []
        out.append(part)
        seen.add(marker)
    return out


def _spread(run: List[Slot], value: str, texts: Dict[str, str]) -> Dict[str, str]:
    """一項資料填進一串連續的空格：先當日期解析，不行再照空格後面印的單位切。

    「民國　＿　年　＿　月　＿　日」配生日、「自＿年＿月／至＿年＿月」配任職期間都是
    這個形態，而模型往往只指名第一格。所以不是只看被指名的格子，而是從被指名那一格
    往後看整串空格（跨段落也算，「自…」與「至…」是同一格裡的兩段）；
    切不開就只填第一格——整串值塞進第一格比留白還糟。
    """
    markers = [_marker_after(texts[x.id], x.end) for x in run]
    parts = _date_parts(value, markers)
    if not parts:
        for k in range(len(run), 1, -1):
            if all(markers[:k]):
                parts = _split_by_markers(value, markers[:k])
                if parts:
                    break
    if not parts:
        return {run[0].id: _pad(run[0], value)}
    return {x.id: _pad(x, _roc(texts[x.id], x, part, marker))
            for x, part, marker in zip(run, parts, markers)}


def _bigrams(text: str) -> set:
    """連續兩個字的詞，只留含中文、又不是雜訊的。

    「in」（Windows 跟 Linux 都有）、「年月」（離職年月跟服役期間「自＿年＿月」都有）、
    「工作」（工作內容跟「您以前在工作上有什麼貢獻」都有）這種詞兩邊都出現，
    卻不代表相關，拿來比對只會放行配錯的資料。
    """
    t = _squash(text)
    return {b for b in (t[i:i + 2] for i in range(len(t) - 1))
            if re.search(r"[一-鿿]", b) and b not in GENERIC_WORDS
            and not re.fullmatch(r"[年月日時分秒]{2}", b)}


def _around(slot: Slot) -> str:
    """位置周圍印的字：欄名，加上「管著這個位置的那一段」——從前一個冒號或換行到 ▁，
    再帶一小段後面的字。

    整格的字全算進來的話，「役別：▁　(如免役說明原因)：▁」這種一格兩欄的位置，
    兩個空格比對出來的結果一模一樣，配對對調了也看不出來。
    「本公司」「本人」拿掉：那是指發表格的公司與填表人自己，不是資料裡的哪一家公司、
    哪一個人。留著的話，推薦人的公司會因為「公司」兩個字被填進「您對本公司的了解」。
    """
    # 一格分好幾段時，標籤常只印在第一段（「負債狀況：」在第一段，勾選框在第二段）
    label = ""
    if slot.para > 0:
        first = slot.cell.paras[0].text.strip()
        label = first if first.endswith(("：", ":")) else ""
    text = slot.preview
    i = text.find("▁")
    if i < 0:
        near = text
    else:
        head = text[:i].rstrip("：: 　")
        near = head[max(head.rfind(c) for c in "：:↵。") + 1:] + text[i:i + 8]
    return re.sub(r"本公司|本人", "", slot.cell.row_head + slot.cell.col_head + label + near)


def _aliases(key: str) -> List[str]:
    """產品對照表裡這個欄位的別名：「手機」也是行動電話、「服務單位」也是公司名稱。

    schema.LABEL_ALIASES 是產品累積下來的表格用語，比對不到共同的詞時靠它——
    「手機」跟「行動電話」一個字都不一樣，光看字面永遠對不上。
    """
    template = re.sub(r"\[\d+\]", "[]", key)
    return [label for label, mapped in LABEL_ALIASES.items() if mapped == template]


def _support(slot: Slot, key: str, fields: Dict[str, str]) -> int:
    """這個位置周圍印的字，跟這項資料有幾個詞（連續兩字）相同。

    比對的詞是資料的名稱；一筆一筆的清單資料再加上同一筆的「關係」欄——
    「您將推薦上述哪位主管」跟「諮詢人姓名」沒有共同的詞，靠的是關係欄的「直屬主管」。
    只加關係欄、不加其他值：公司名（「○○公司」）會跟「您對本公司的了解」對上「公司」，
    放行把部門填進問答題；證照機構「電腦技能基金會」會跟「電腦操作」對上「電腦」。
    欄名印英文時（「E-Mail」）中文名稱一個字都對不上，改比代碼本身。
    """
    spec = _spec(key)
    entry = re.match(r"(\w+\[\d+\])\.", key)
    relation = fields.get(f"{entry.group(1)}.relation", "") if entry else ""
    words = _bigrams(spec.label if spec else "") | _bigrams(relation)
    around = _around(slot)
    word = key.split(".")[-1].replace("_", "").lower()
    letters = re.sub(r"[^a-z]", "", around.lower())
    squashed = _squash(around)
    return (len(words & _bigrams(around))
            + (1 if word and word in letters else 0)
            + (2 if any(a in squashed for a in _aliases(key)) else 0))


def _plausible(slot: Slot, key: str, fields: Dict[str, str]) -> bool:
    """擋掉明顯放錯地方的配對。

    選項型（有／無、是／否）與長文（自傳）的值，離開印著它名稱的地方就沒有意義：
    「否」寫進「直屬主管姓名」、「無」寫進問答題、自傳寫進「本業專長」，都是模型
    拿不相干的資料頂替。所以這兩種資料寫成字時，那一格附近必須印著它的名稱；
    勾選框不受此限（「□否 □是」本來就不會印出資料名稱）。

    一筆一筆的清單資料（學經歷、家人、推薦人）至少要跟那一格的欄名，或同一筆的
    其他資料，有一個詞相同：公司名被放進家庭成員的「姓名」欄時，兩邊一個詞都
    對不上。同一筆的其他資料要算進來，是因為「您將推薦上述哪位主管」這種問句跟
    「諮詢人姓名」沒有共同的詞，靠的是同一筆的關係欄「直屬主管」。
    """
    spec = _spec(key)
    around = _squash(_around(slot))
    if slot.kind != "box" and spec and spec.kind in ("choice", "longtext"):
        return _squash(spec.label) in around
    if re.match(r"\w+\[\d+\]\.", key):
        return _support(slot, key, fields) > 0
    if slot.kind == "box":
        return True
    # 其他單項資料寫成字時，名稱至少要有一個字出現在那一格附近；英文欄名比對代碼
    # （「E-mail」對 contact.email）。擋的是「語言:1.」後面填興趣、填本人姓名這種
    word = key.split(".")[-1].replace("_", "").lower()
    return (bool(set(re.findall(r"[一-鿿]", spec.label if spec else "")) & set(around))
            or word in re.sub(r"[^a-z]", "", around.lower())
            or any(a in around for a in _aliases(key)))


def _as_tick(by_id: Dict[str, Slot], out: Dict[str, str], fields: Dict[str, str]) -> None:
    """值就是框旁邊印的選項時，那一項用勾表達，不是寫在同一格的空位上。

    「負債狀況：▁　□無　□卡債：$」——模型指的那一格沒錯，但這一格填的是勾 ■無，
    在冒號後面再寫一個「無」就多出一個字。同一格裡有字面對得上的框就讓給框。

    只在這一格整個就一題勾選題時才動。宣告事項那種一格印六題的，對得上的框
    很可能是別題的，換過去就勾錯行。
    """
    by_addr: Dict[str, List[Slot]] = {}
    for x in by_id.values():
        by_addr.setdefault(x.addr, []).append(x)
    for sid, key in list(out.items()):
        slot = by_id[sid]
        if slot.kind == "box":
            continue
        boxes = [b for b in by_addr[slot.addr] if b.kind == "box"]
        if len(box_groups(boxes)) != 1:
            continue
        box = next((b for b in boxes if b.id not in out
                    and _ticked(b.option, fields.get(key, ""))), None)
        if box is not None:
            log.info("改用勾的 %s：%s → %s", key, sid, box.id)
            del out[sid]
            out[box.id] = key


def dedupe(slots: List[Slot], chosen: Dict[str, str], fields: Dict[str, str],
           trusted: frozenset = frozenset()) -> Dict[str, str]:
    """先擋掉放錯地方的配對，再讓同一項資料在同一張表格裡只留一個格子：
    搶同一項的格子裡，欄名跟資料名稱最像的那格贏，平手看誰在前面。

    trusted 是「整張表問過欄名」得到的配對，不再用詞比對檢查——模型把「服務單位」
    判成公司名稱是對的，但那兩個詞一個字都不一樣，用詞比對會把整欄擋掉。

    模型的錯多半是「找不到更好的就拿用過的頂替」：學歷表列數比資料多就把第一筆
    往下每列再填一次、住家電話欄填行動電話、業外專長欄再填一次本業專長。
    同一張表格內不重複，這些一次全部擋掉。跨表格不管——姓名出現在表頭又出現在
    簽名欄是正常的。同一格內重複也不管：「□未婚 □已婚」兩個框都指向婚姻狀況。
    """
    by_id = {s.id: s for s in slots}
    out = {sid: key for sid, key in chosen.items()
           if sid in trusted or _plausible(by_id[sid], key, fields)}
    _as_tick(by_id, out, fields)
    order: Dict[str, int] = {}
    claims: Dict[Tuple[str, str], Dict[str, List[Slot]]] = {}
    for i, slot in enumerate(slots):                  # slots 照文件順序
        order.setdefault(slot.addr, i)
        key = out.get(slot.id)
        if key:
            table = slot.addr.split(".")[0]
            claims.setdefault((table, key), {}).setdefault(slot.addr, []).append(slot)
    for (_table, key), by_cell in claims.items():
        # 只留第一個搶到的格子的話，早一步放錯的會把對的偷走：公司名先被放進
        # 家庭成員欄，後面服務單位欄就拿不到了
        winner = max(by_cell, key=lambda a: (any(x.id in trusted for x in by_cell[a]),
                                             max(_support(x, key, fields) for x in by_cell[a]),
                                             -order[a]))
        for addr, group in by_cell.items():
            for slot in (group[1:] if addr == winner else group):
                # 同一格裡的勾選框與空格可以共用同一項資料：「□未婚 □已婚」兩個框都指向
                # 婚姻狀況、「□　8 月 24 日」是勾一個框再把日期寫進後面的空格。
                # 整段值的位置（空格子、冒號後面、問句下一行）就不行——問答格常有
                # 「問句後面」和「空白段」兩個位置，兩個都填會把整段答案寫兩次
                if addr == winner and {group[0].kind, slot.kind} <= {"box", "gap"}:
                    continue
                del out[slot.id]

    # 同一格內的配對重排：模型偶爾把兩個位置對調（「役別：＿(如免役說明原因)：＿」
    # 填成「癲癇／免役」）。各自的欄名分得出來的話就換回來
    in_cell: Dict[str, List[Slot]] = {}
    for slot in slots:
        if out.get(slot.id) and slot.kind != "box":
            in_cell.setdefault(slot.addr, []).append(slot)
    for addr, group in in_cell.items():
        if not 2 <= len(group) <= 4:
            continue
        keys = [out[x.id] for x in group]
        best = None
        score = sum(_support(x, k, fields) for x, k in zip(group, keys))
        for perm in permutations(keys):
            if perm == tuple(keys):
                continue
            gain = sum(_support(x, k, fields) for x, k in zip(group, perm))
            if gain > score and all(_plausible(x, k, fields) for x, k in zip(group, perm)):
                best, score = perm, gain
        if best:
            log.info("同一格內重排 %s：%s → %s", addr, keys, list(best))
            for x, k in zip(group, best):
                out[x.id] = k

    # 跨表格可以重複（離職原因同時是工作經歷欄與「為何離職」問答題的答案），
    # 但第二張表那裡必須印著跟它有關的字——模型會把本人姓名塞進「語言:1.」後面
    by_key: Dict[str, List[Slot]] = {}
    for slot in slots:
        if out.get(slot.id):
            by_key.setdefault(out[slot.id], []).append(slot)
    for key, group in by_key.items():
        best = max(group, key=lambda x: (x.id in trusted, _support(x, key, fields)))
        for slot in group:
            if (slot.id not in trusted
                    and slot.addr.split(".")[0] != best.addr.split(".")[0]
                    and _support(slot, key, fields) == 0):
                del out[slot.id]
    return out


def apply_fills(slots: List[Slot], chosen: Dict[str, str], fields: Dict[str, str],
                ticks: Dict[str, bool] = None, highlight: bool = False) -> int:
    """把選中的值寫回文件。ticks 是 ask_boxes 判斷過意思的勾選框，有給就照它勾。
    highlight 把填進去的字標成黃底，只給網頁預覽用。"""
    ticks = ticks or {}
    texts = {x.id: x.cell.paras[x.para].text for x in slots}
    reps: Dict[str, str] = {}
    by_cell: Dict[str, List[Slot]] = {}
    by_para: Dict[Tuple[str, int], List[Slot]] = {}
    for slot in slots:
        by_cell.setdefault(slot.addr, []).append(slot)
        by_para.setdefault((slot.addr, slot.para), []).append(slot)

    for _addr, group in by_cell.items():
        # 先處理勾選框：選項字面對得上值就勾；對不上的照 ask_boxes 判斷的意思勾
        for slot in group:
            value = fields.get(chosen.get(slot.id) or "", "")
            if slot.kind == "box" and slot.id in ticks:
                reps[slot.id] = CHECKED if ticks[slot.id] else ""
            elif value and slot.kind == "box":
                reps[slot.id] = _replacement(slot, value)
        # 已經用打勾表達過的資料，不要在同一格再寫一次字：「■良好」勾好了，
        # 後面「請說明原因：」就不該再補一個「良好」
        ticked = {chosen.get(x.id) for x in group if x.kind == "box" and reps.get(x.id)} - {None}
        for slot in group:
            key = chosen.get(slot.id)
            value = fields.get(key or "")
            if value and slot.kind not in ("gap", "box") and key not in ticked:
                reps[slot.id] = _replacement(slot, value)

        # 空格以「整格」為單位：被指名的那一格往後看，把同一項資料攤到連續的空格上。
        # 跨段落也要算——「自　年　月」與「至　年　月」是同一格裡的兩段，
        # 卻是同一個任職期間
        gaps = [x for x in group if x.kind == "gap" and chosen.get(x.id) not in ticked]
        i = 0
        while i < len(gaps):
            key = chosen.get(gaps[i].id)
            if not fields.get(key or ""):
                i += 1
                continue
            run = [gaps[i]]
            while i + len(run) < len(gaps) and chosen.get(gaps[i + len(run)].id) in (None, key):
                run.append(gaps[i + len(run)])
            reps.update(_spread(run, fields[key], texts))
            i += len(run)
        # 日期單位前面的空格只收數字：「自＿年＿月」塞進一個公司名一定是配錯了
        for gap in gaps:
            if (gap.option in DATE_UNITS and reps.get(gap.id)
                    and not re.fullmatch(r"[\d.,]+", reps[gap.id].strip())):
                del reps[gap.id]

    for (_addr, _pi), group in by_para.items():
        text = texts[group[0].id]
        for line in {text.count("\n", 0, x.start) for x in group}:
            here = [x for x in group if text.count("\n", 0, x.start) == line]
            if not any(x.kind == "box" and reps.get(x.id) for x in here):
                for filled in [x for x in here if x.kind in ("gap", "append") and reps.get(x.id)
                               and _kind(chosen.get(x.id) or "") == "date"]:
                    # 勾離那個空格最近的前一個框：「□隨時 □　週 □　8 月 24 日」
                    # 要勾的是第三個。不管那個框被指到哪一項資料——選項本身沒有字
                    # （後面接的是空格）時，模型指到誰都一樣，位置才是意思所在
                    near = [x for x in here if x.kind == "box" and x.start < filled.start
                            and x.id not in ticks]
                    if near:
                        reps[max(near, key=lambda x: x.start).id] = CHECKED
                        break
            # 勾選題後面接的「請說明：」「姓名及部門：」「其他：」是補充說明，只有勾了
            # 前面的選項才寫：這一行勾的是「否／無／不…」、或整行一個都沒勾，就不寫字
            boxes = [x for x in here if x.kind == "box"]
            if not boxes:
                continue
            picked = [x for x in boxes if reps.get(x.id)]
            if picked and not any(re.fullmatch(r"否|無|沒有|不.*", x.option) for x in picked):
                continue
            for x in here:
                if x.kind != "box" and x.start > boxes[0].start:
                    reps.pop(x.id, None)

    written = 0
    for (_addr, pi), group in by_para.items():
        changes = [(x.start, x.end, reps[x.id]) for x in group if reps.get(x.id)]
        if not changes:
            continue
        for slot in group:
            if reps.get(slot.id):
                written += 1
                log.debug("寫 %-18s %-26s %s", slot.id, chosen.get(slot.id),
                          reps[slot.id].replace("\n", "↵")[:40])
        # 由左而右串起來：原本的字、填進去的字、原本的字…。位置彼此不重疊，
        # 所以串出來的結果跟由右往左逐一替換是一樣的，但這樣才分得出哪幾段是新的
        text, parts, cursor = texts[group[0].id], [], 0
        for start, end, rep in sorted(changes):
            parts += [(text[cursor:start], False), (rep, True)]
            cursor = end
        parts.append((text[cursor:], False))
        para = group[0].cell.paras[pi]
        if highlight:
            _set_para_marked(para, parts)
        else:
            _set_para(para, "".join(t for t, _ in parts))
    return written


ROWS_SYSTEM = """表格裡有幾張「一列填一筆」的表（學歷、工作經歷、家庭成員這類），
下面列出每一張表的欄名。請判斷每一欄要填清單資料的哪一個子欄位。

規則：
1. 只能從給定的代碼挑；那一欄在個人資料裡沒有對應，填 __無__。
2. 看欄名的意思判斷：「服務單位」是公司名稱、「工作期間」是任職期間。
3. 同一張表左右印了兩組一樣的欄名（家庭成員常見），兩組都照樣對應。"""


def _col(slot: Slot) -> int:
    return int(slot.addr.split(".")[2][1:])


def row_blocks(slots: List[Slot]) -> Dict[Tuple[str, int], List[Slot]]:
    """一列一筆的表：左邊整排沒有欄位名稱、上面有欄名的格子，依欄名那一列分組。

    每一格挑代表位置：空格子就是它自己；只印格式的格子（「自　年　月／至　年　月」）
    取第一個空格，值由 _spread 往後攤；整格都是勾選框的（「□畢 □肄」）取全部框。
    """
    out: Dict[Tuple[str, int], List[Slot]] = {}
    by_cell: Dict[str, List[Slot]] = {}
    for x in slots:
        if x.cell.depth:
            by_cell.setdefault(x.addr, []).append(x)
    for addr, group in by_cell.items():
        kinds = {x.kind for x in group}
        if "blank" in kinds:
            rep = [x for x in group if x.kind == "blank"][:1]
        elif kinds == {"box"}:
            rep = group
        elif "gap" in kinds:
            rep = [x for x in group if x.kind == "gap"][:1]
        else:
            continue
        table, row = addr.split(".")[:2]
        out.setdefault((table, int(row[1:]) - group[0].cell.depth), []).extend(rep)
    return out


def ask_rows(blocks: Dict[Tuple[str, int], List[Slot]], fields: Dict[str, str],
             images: List[bytes], host: str = LLM_HOST,
             model: str = LLM_MODEL) -> Dict[str, str]:
    """一列一筆的表：問模型「每一欄是什麼」，再由程式照「往下第 N 列＝第 N 筆」排進去。

    逐格問時，這種表的每一格都長得一模一樣（空的，只差欄名），模型會把第二筆
    錯位到第三列、或整列漏掉第三筆。但「服務單位公司行號這一欄是公司名稱」
    它答得很穩——欄的意思交給模型，列的順序交給程式。
    """
    roots = {k.split("[")[0] for k in fields if "[" in k}
    choices = [k for k in BY_KEY if "[]" in k and k.split("[]")[0] in roots]
    ids, lines, allowed = [], [], {}
    for bi, group in enumerate(blocks.values(), 1):
        section = (next((x.cell.row_head for x in group if x.cell.row_head), "")
                   or next((x.cell.caption for x in group if x.cell.caption), ""))
        # 這一區標了是誰（「家庭成員」、「請列舉…並同意我們諮詢」），可選的就只剩
        # 那一組。不先收斂的話，欄名「稱謂／姓名／服務機關／職位」跟諮詢人太像，
        # 整張表會被對到另一份名單。比對前要抹空白：標題常寫成「家　庭　成　員」
        picks = [k for root, words in OTHER_PEOPLE.items()
                 if any(w in _squash(section) for w in words)
                 for k in choices if k.startswith(root)]
        lines.append(f"表{bi}" + (f"（這一區印著「{_squash(section)[:40]}」）" if section else "") + "：")
        for c, head in sorted({(_col(x), x.cell.col_head) for x in group}):
            ids.append(f"b{bi}c{c}")
            allowed[f"b{bi}c{c}"] = picks or choices
            lines.append(f"  b{bi}c{c}｜欄名：{_squash(head)}")
    schema = {
        "type": "object",
        "properties": {i: {"type": "string", "enum": allowed[i] + [NONE]} for i in ids},
        "required": ids,
        "additionalProperties": False,
    }
    user: List[Dict[str, Any]] = [{"type": "text", "text": (
        "可用的代碼（附第一筆的值，讓你對得上欄名）：\n"
        + "\n".join(f"- {k}{_label(k)}"
                    + (f"：{fields[k.replace('[]', '[0]')][:20]}"
                       if k.replace("[]", "[0]") in fields else "")
                    for k in choices)
        + "\n\n各表的欄名：\n" + "\n".join(lines))}]
    for img in images:
        user.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")}})
    data = llm.ask(host, ROWS_SYSTEM, user, schema, model=model,
                   label=f"一列一筆:{len(blocks)}表")

    out: Dict[str, str] = {}
    for bi, group in enumerate(blocks.values(), 1):
        mapping = {_col(x): data.get(f"b{bi}c{_col(x)}") for x in group}
        mapping = {c: k for c, k in mapping.items() if k in choices}
        # 一張表就是一份清單。欄位分屬兩份清單時，少數派的那幾欄是模型串了行——
        # 工作經歷表的「直屬主管姓名」問的是這一列的主管，不是諮詢人那一份名單
        if mapping:
            main = Counter(k.split("[")[0] for k in mapping.values()).most_common(1)[0][0]
            mapping = {c: k for c, k in mapping.items() if k.split("[")[0] == main}
        # 一列放幾筆，看表格自己印了幾組同樣的欄名（家庭成員左右各一組「姓名 稱謂
        # 年齡 職業」），由左而右、由上而下編號。不能看模型的答案——模型只要把兩欄
        # 對到同一個子欄位，整張表就被當成一列兩筆，第二列拿到第三筆
        heads = {c: _squash(h) for c, h in {(_col(x), x.cell.col_head) for x in group}}
        per_row = max(Counter(heads.values()).values(), default=1)
        seen: Counter = Counter()
        nth = {}
        for c in sorted(heads):
            nth[c] = seen[heads[c]]
            seen[heads[c]] += 1
        log.info("一列一筆 表%d：%s", bi, {c: mapping[c] for c in sorted(mapping)})
        for x in group:
            c = _col(x)
            if c in mapping:
                key = mapping[c].replace("[]", f"[{(x.cell.depth - 1) * per_row + nth[c]}]")
                if key in fields:
                    out[x.id] = key
    return out


RECALL_SYSTEM = """表格上還有幾個位置空著，個人資料裡也還有沒填進去的項目。
每個位置後面列了幾項名稱相近、還沒用到的資料。每個位置先挑一項（pick），
再判斷挑的這一項是不是真的就是那一格要的東西（answers）。

規則：
1. pick：只能從那個位置列出的項目裡挑；都不對就填 __無__。
2. answers：挑的資料就是那一格問的東西才填「是」，只是字面有點像不算。
   例如位置問「目前住址」、資料是「緊急聯絡人的住址」，要填「否」。"""


def _placed(slots: List[Slot], chosen: Dict[str, str],
            fields: Dict[str, str]) -> set:
    """實際會寫出東西的資料項。

    「被指派過」不等於「填進去了」：日期被指到「□隨時」那個框，勾不起來、也沒寫進
    任何空格，等於沒填，要留給補漏。選項型的資料（婚姻、健康、宣告事項）被指到
    勾選框就算表達完了——字面對不上（資料「無」、表格印「□否」）由 ask_boxes 判斷。
    """
    out = set()
    for s in slots:
        key = chosen.get(s.id)
        value = fields.get(key or "")
        if value and (s.kind != "box" or _ticked(s.option, value) or _kind(key) == "choice"):
            out.add(key)
    return out


def recall(slots: List[Slot], chosen: Dict[str, str], fields: Dict[str, str],
           images: List[bytes], skip: frozenset = frozenset(),
           host: str = LLM_HOST, model: str = LLM_MODEL) -> Dict[str, str]:
    """補漏：還空著的位置，只拿「名稱相近、還沒用到」的資料再問一次。

    第一輪一百多個位置逐個問，模型對某一格想不出來就答 __無__，漏掉的不會再有人問。
    反過來問「這幾十項資料各該去哪」也試過：9B 模型把五十幾項全塞進同一個位置。
    改成每個空位置只列出跟它有共同詞的幾項未用資料——選項少、每一項都跟那一格
    沾得上邊，是小得多的一道題。
    """
    placed = _placed(slots, chosen, fields)
    left = [k for k in fields if k not in placed]
    todo = []
    for s in slots:
        # 看單一位置還空不空就好：同一行勾好了「■免役」，後面的「原因＿＿」還是要補
        if s.id in chosen or s.id in skip:
            continue
        # 勾選框：資料的值就是印在框旁邊的選項（「畢」對「□畢」），或至少有共同的詞
        # （「104 人力銀行」對「□人力網站」），是比欄名更強的線索
        ranked = sorted(((_support(s, k, fields)
                          + (2 if s.kind == "box" and (_ticked(s.option, fields[k])
                             or _bigrams(fields[k]) & _bigrams(s.option)) else 0), k)
                         for k in left), reverse=True)
        cands = [k for score, k in ranked if score > 0 and _plausible(s, k, fields)][:6]
        if cands:
            todo.append((s, cands))
    if not todo:
        return {}

    ids = [f"s{i}" for i in range(1, len(todo) + 1)]
    # 先挑再判斷「是不是真的在回答那一格」：只挑的話，模型會因為兩邊都有「公司」
    # 就把推薦人的公司填進「您對本公司的了解」——字面對得上，問的卻不是同一家公司
    schema = {
        "type": "object",
        "properties": {i: {
            "type": "object",
            "properties": {"pick": {"type": "string", "enum": cands + [NONE]},
                           "answers": {"type": "string", "enum": ["是", "否"]}},
            "required": ["pick", "answers"],
            "additionalProperties": False,
        } for i, (_s, cands) in zip(ids, todo)},
        "required": ids,
        "additionalProperties": False,
    }
    lines = [f"  {i}｜{_describe(s)}\n      可選：" + "、".join(
        f"{k}{_label(k)}＝{fields[k][:20]}" for k in cands) for i, (s, cands) in zip(ids, todo)]
    user: List[Dict[str, Any]] = [{"type": "text", "text": "空著的位置與可選的資料：\n" + "\n".join(lines)}]
    for img in images:
        user.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")}})
    data = llm.ask(host, RECALL_SYSTEM, user, schema, model=model,
                   label=f"補漏:{len(ids)}處")
    out = {}
    for i, (s, cands) in zip(ids, todo):
        ans = data.get(i) or {}
        if ans.get("pick") not in cands:
            continue
        if ans.get("answers") != "是":
            log.info("補漏不採用 %s → %s（模型判斷不是那一格要的）", ans["pick"], s.id)
            continue
        out[s.id] = ans["pick"]
        log.info("補漏 %s → %s", ans["pick"], s.id)
    return out


BOXES_SYSTEM = """表格上的勾選題。每一題給你題目印的字和它的選項，請回答兩件事：
這一題在問個人資料的哪一項（field），以及照那一項的值該勾哪一個選項（pick）。

規則：
1. field 只能從項目代碼裡挑。個人資料沒有對應的、或這題是公司自己要填的，填 __無__。
2. pick 只能從那一題印出來的選項裡挑，意思相同就算：資料「無」對選項「否」、
   「中等」對「尚可」、「104 人力銀行」對「人力網站」、「普通重型機車」對「機車」。
   意思都不符就填 __無__。
3. 題目跟資料講的不是同一件事，兩個都填 __無__——題目問「可配合加班」而資料是
   「擔任主管」，選項雖然都是是非，卻不是同一件事。
4. 可以複選的題目（電腦操作、交通工具）挑一個就好，其餘對得上的由程式補。"""


def box_groups(slots: List[Slot]) -> Dict[Tuple[str, int, int], List[Slot]]:
    """勾選題分組：同一格、同一段、同一行的框算一題（宣告事項一格印七題）。"""
    out: Dict[Tuple[str, int, int], List[Slot]] = {}
    for x in slots:
        if x.kind == "box":
            line = x.cell.paras[x.para].text.count("\n", 0, x.start)
            out.setdefault((x.addr, x.para, line), []).append(x)
    return out


def ask_boxes(groups: Dict[Tuple[str, int, int], List[Slot]], fields: Dict[str, str],
              images: List[bytes], chosen: Dict[str, str], narrow: bool = False,
              host: str = LLM_HOST,
              model: str = LLM_MODEL) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """勾選題自己問一輪：這一題在問哪一項資料、該勾哪一個選項。

    勾選題跟填空題是兩種題目。夾在一百多個位置裡逐格問時，模型要順便判斷「□尚可」
    在問什麼，常常答不出來；而且表格印的字跟資料的寫法往往不同（「中等」對「尚可」、
    「104 人力銀行」對「人力網站」），那是語意問題，要把題目、選項、資料擺在一起
    才判得準。回傳 {位置編號: 資料代碼} 與 {位置編號: 勾不勾}。
    """
    items = list(groups.items())
    if len(items) > BOX_BATCH:      # 一次問太多題，模型會整批答 __無__
        # 平均分攤，不要留下落單的一題：17 題分成 8+8+1 時，最後那一批只有一題、
        # 前後都沒有同類的題目可以參照，實測整批答 __無__。分成 6+6+5 就好了
        size = -(-len(items) // -(-len(items) // BOX_BATCH))
        picked, ticks = {}, {}
        for i in range(0, len(items), size):
            part = dict(items[i:i + size])
            got, tk = ask_boxes(part, fields, images, {**chosen, **picked}, narrow,
                                host, model)
            picked.update(got)
            ticks.update(tk)
        return picked, ticks
    ids = [f"q{i}" for i in range(1, len(groups) + 1)]
    options, lines, picks = [], [], []
    for qid, ((_addr, pi, line), boxes) in zip(ids, groups.items()):
        printed = boxes[0].cell.paras[pi].text.split("\n")[line]
        opts = list(dict.fromkeys(b.option for b in boxes if b.option))
        options.append(opts)
        ctx = _squash(boxes[0].cell.row_head + boxes[0].cell.col_head)[:20]
        # 附上幾個可能相關的資料：欄名對得上、值就是印在框旁邊的選項、或同一列已經
        # 填過同一群的資料（「語言:1.英文」旁邊那題八成就是問語文程度）。
        # 一百多個項目裡要模型自己撈，實測駕照、負債狀況這種明明對得上的都會漏掉
        row = boxes[0].addr.rsplit(".", 1)[0]
        near = {chosen[k].split(".")[0] for k in chosen if k.rsplit(".", 1)[0].startswith(row)}
        ranked = sorted(((_support(boxes[0], k, fields)
                          + (2 if any(_ticked(b.option, v) or _bigrams(v) & _bigrams(b.option)
                                      for b in boxes) else 0)
                          + (1 if k.split(".")[0] in near else 0), k)
                         for k, v in fields.items()), reverse=True)
        # 同一個欄位只列一次：三筆工作經歷的「擔任主管＝否」長得一模一樣，
        # 不去掉就把四個名額佔滿，真正對得上的那一項擠不進來
        best, seen = [], set()
        for score, k in ranked:
            template = re.sub(r"\[\d+\]", "[]", k)
            if score > 0 and template not in seen and len(best) < 4:
                seen.add(template)
                best.append(k)
        hint = "、".join(f"{k}{_label(k)}＝{fields[k][:14]}" for k in best)
        picks.append(best)
        lines.append(f"  {qid}｜{_squash(printed)[:70]}" + (f"｜欄名:{ctx}" if ctx else "")
                     + (f"\n      可能相關：{hint}" if hint else ""))
    schema = {
        "type": "object",
        "properties": {qid: {
            "type": "object",
            "properties": {"field": {"type": "string",
                                     "enum": (cands if narrow else list(fields)) + [NONE]},
                           "pick": {"type": "string", "enum": opts + [NONE]}},
            "required": ["field", "pick"],
            "additionalProperties": False,
        } for qid, opts, cands in zip(ids, options, picks)},
        "required": ids,
        "additionalProperties": False,
    }
    user: List[Dict[str, Any]] = [{"type": "text", "text": (
        "個人資料（項目代碼：值）：\n"
        + "\n".join(f"- {k}{_label(k)}：{v[:60]}" for k, v in fields.items()))}]
    for img in images:
        user.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")}})
    user.append({"type": "text", "text": "要判斷的勾選題：\n" + "\n".join(lines)})
    data = llm.ask(host, BOXES_SYSTEM, user, schema, model=model,
                   label=f"勾選題{'再問' if narrow else ''}:{len(ids)}題")

    picked: Dict[str, str] = {}
    ticks: Dict[str, bool] = {}
    for qid, (_g, boxes) in zip(ids, groups.items()):
        ans = data.get(qid) or {}
        key = ans.get("field")
        value = fields.get(key or "")
        if not value:
            continue
        pick = ans.get("pick")
        for b in boxes:
            picked[b.id] = key
            # 字面對得上的都勾（複選題「Windows、Word、Excel」一次勾三個），
            # 加上模型判斷意思相同的那一個
            ticks[b.id] = _ticked(b.option, value) or (bool(b.option) and b.option == pick)
        log.info("勾選題 %s %s＝%s → 勾 %s", boxes[0].addr, key, value[:20], pick)
    return picked, ticks


def obvious_boxes(groups: Dict[Tuple[str, int, int], List[Slot]], fields: Dict[str, str],
                  picked: Dict[str, str], ticks: Dict[str, bool]) -> Dict[str, str]:
    """模型沒答出來的勾選題，程式自己認得出來的就自己填。

    認的條件是兩邊都成立：題目旁邊印的就是某一項資料的名稱（產品對照表裡的別名，
    「資訊來源」就是招募管道），而且那一項的值跟其中一個選項有共同的詞（「104 人力
    銀行」對「人力網站」）。兩個條件湊在一起已經不是猜——名稱指定了問的是哪一項，
    值指定了該勾哪一個。缺一個就放著不填：「□其他」旁邊也印著「健康狀況」，
    可是健康狀況的值「優」對不上「其他」，那就不是它。

    名稱有好幾個對得上時取離這個位置最近的：一列印兩組欄位是常態，「資訊來源」
    那一格的左邊就是「健康狀況：□優□良」，兩個名稱都在周圍的字裡。
    """
    out: Dict[str, str] = {}
    for boxes in groups.values():
        if any(b.id in picked for b in boxes):
            continue
        around = _squash(_around(boxes[0]))

        def near(key: str) -> int:      # 名稱印在哪裡，越靠近這個位置越可能是它
            spec = _spec(key)
            return max(around.rfind(a)
                       for a in _aliases(key) + [spec.label if spec else ""] if a)

        named = sorted(((pos, k) for pos, k in ((near(k), k) for k in fields)
                        if pos >= 0), reverse=True)
        if not named or (len(named) > 1 and named[0][0] == named[1][0]):
            continue
        key = named[0][1]
        hit = {b.id: _ticked(b.option, fields[key])
               or bool(_bigrams(b.option) & _bigrams(fields[key])) for b in boxes}
        if not any(hit.values()):
            continue
        for b in boxes:
            out[b.id] = key
            ticks[b.id] = hit[b.id]
        log.info("欄名就是它 %s %s＝%s → 勾 %s", boxes[0].addr, key, fields[key][:16],
                 "、".join(b.option for b in boxes if hit[b.id]))
    return out


def _batches(slots: List[Slot]) -> List[List[Slot]]:
    """分批時同一個格子的位置不拆開——「優點：」「缺點：」被拆到兩批問，
    模型看不到彼此就容易錯位。"""
    out: List[List[Slot]] = []
    for _addr, group in groupby(slots, key=lambda s: s.addr):
        group = list(group)
        if out and len(out[-1]) + len(group) <= BATCH:
            out[-1] += group
        else:
            out.append(group)
    return out


@dataclass
class Draft:
    """一份表格分析完的結果。

    slots 不存進資料庫——它牽著 python-docx 的段落物件。要用的時候照原檔重新
    解析一次就好：解析純粹是程式，同一份文件跑幾次結果都一樣，約 0.2 秒。
    存下來的只有 assignment 與 ticks，那兩個才是模型（和使用者）的決定。
    """
    slots: List[Slot]
    fields: Dict[str, str]          # 這份表格用得到的個人資料
    assignment: Dict[str, str]      # 位置編號 -> 項目代碼
    ticks: Dict[str, bool]          # 位置編號 -> 勾不勾


def parse(blank: Path) -> Tuple[Any, List[Cell], List[Slot]]:
    """把文件拆成格子與可寫位置。沒有模型。"""
    doc = Document(str(blank))
    form = cells(doc)
    return doc, form, [s for cell in form for s in slots_of(cell)]


def usable_fields(form: List[Cell], profile: Dict[str, Any]) -> Dict[str, str]:
    """這份表格用得到的個人資料。

    別人的資料（緊急聯絡人、家人、諮詢人）只在表格提到那個人時才拿出來給模型挑：
    沒提到還留在清單裡，模型會把緊急聯絡人的電話填進本人的住家電話欄。
    比對前抹掉空白——標題常寫成「家　庭　成　員」，不抹就對不上「家庭」。
    """
    printed = _squash("".join(p.text for cell in form for p in cell.paras))
    return {k: v for k, v in fields_of(profile).items()
            if not any(k.startswith(root) and not any(w in printed for w in words)
                       for root, words in OTHER_PEOPLE.items())}


def analyze(blank: Path, profile: Dict[str, Any],
            host: str = LLM_HOST, model: str = LLM_MODEL) -> Draft:
    """看著版面決定每一個位置放哪一項資料。不寫檔——寫檔是 write() 的事，
    中間留給使用者修正。"""
    doc, form, slots = parse(blank)
    fields = usable_fields(form, profile)
    images = render_pages(doc)
    log.info("%s：可寫位置 %d 處、資料 %d 項、示意圖 %d 張",
             blank.name, len(slots), len(fields), len(images))

    chosen: Dict[str, str] = {}
    # 一列一筆的表先整張問「每一欄是什麼」，那些格子就不再逐格問、也不參與補漏
    blocks = row_blocks(slots)
    block_cells = {x.addr for group in blocks.values() for x in group}
    in_blocks = frozenset(x.id for x in slots if x.addr in block_cells)
    if blocks:
        try:
            chosen.update(ask_rows(blocks, fields, images, host, model))
        except llm.LlmError as e:
            log.warning("一列一筆的表判讀失敗，改回逐格問：%s", e)
            in_blocks = frozenset()
    # 勾選題自己問一輪。排在逐格問之後，才看得到同一列已經填了哪些資料
    groups = box_groups([x for x in slots if x.id not in in_blocks])
    in_boxes = frozenset(x.id for g in groups.values() for x in g)
    for n, batch in enumerate(_batches([x for x in slots
                                        if x.id not in in_blocks | in_boxes]), 1):
        try:
            chosen.update(ask(batch, fields, images, chosen, host, model))
        except llm.LlmError as e:      # 一批失敗不該讓整份表格陪葬，其餘照跑、照評分
            log.warning("第 %d 批問失敗：%s", n, e)

    ticks: Dict[str, bool] = {}
    if groups:
        try:
            picked, ticks = ask_boxes(groups, fields, images, chosen,
                                      host=host, model=model)
            # 沒答出來的再問一次，而且只列那一題的候選。十幾題一起問時模型會跳過
            # 幾題（同一題換一批問又答得出來），選項縮到四個以內就是小得多的一道題
            again = {g: bs for g, bs in groups.items() if not any(b.id in picked for b in bs)}
            if again and len(again) < len(groups):
                more, tk = ask_boxes(again, fields, images, {**chosen, **picked},
                                     narrow=True, host=host, model=model)
                picked.update(more)
                ticks.update(tk)
            picked.update(obvious_boxes(groups, fields, picked, ticks))
            chosen.update(picked)
        except llm.LlmError as e:
            log.warning("勾選題判讀失敗：%s", e)
    skip = in_blocks | in_boxes

    # 補漏看去重之後的結果：第一輪被丟掉的配對（住家電話欄填了已經用過的行動電話）
    # 讓出來的位置，也要能再問一次
    kept = dedupe(slots, chosen, fields, trusted=in_blocks)
    try:
        kept = dedupe(slots, {**kept, **recall(slots, kept, fields, images, skip,
                                               host, model)},
                      fields, trusted=in_blocks)
    except llm.LlmError as e:
        log.warning("補漏那一輪失敗：%s", e)
    ticks = {k: v for k, v in ticks.items() if k in kept}   # 去重丟掉的配對，勾選也跟著不算
    log.info("%s：模型指定 %d 處、留下 %d 處", blank.name, len(chosen), len(kept))
    return Draft(slots=slots, fields=fields, assignment=kept, ticks=ticks)


def write(blank: Path, out: Path, assignment: Dict[str, str],
          ticks: Dict[str, bool], profile: Dict[str, Any],
          highlight: bool = False) -> int:
    """把決定好的值寫回文件，存到 out。回傳實際寫了幾處。

    重新解析一份原檔再寫，所以使用者改過 assignment 之後可以再叫一次；
    預覽與正式匯出也是各寫各的，不會互相汙染。
    """
    doc, form, slots = parse(blank)
    written = apply_fills(slots, assignment, usable_fields(form, profile), ticks,
                          highlight)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return written


def solve(blank: Path, profile: Dict[str, Any], out: Path,
          host: str = LLM_HOST, model: str = LLM_MODEL) -> Dict[str, Any]:
    """分析完直接寫檔。研究迴圈的評分走這個入口。"""
    draft = analyze(blank, profile, host, model)
    written = write(blank, out, draft.assignment, draft.ticks, profile)
    log.info("%s：實際寫入 %d 處", blank.name, written)
    # 配對結果一起回報：分數退步時要分得出是「模型配錯」還是「程式寫錯」
    return {"slots": len(draft.slots), "chosen": len(draft.assignment),
            "written": written, "assignment": draft.assignment, "ticks": draft.ticks}

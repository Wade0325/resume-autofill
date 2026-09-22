# -*- coding: utf-8 -*-
"""Cake（cake.me）個人檔案：把「我的資料」裡 Cake 上還沒有的工作經驗、學歷、證照補上去。

- 只新增，不改動 Cake 上已經有的資料：名稱對得上的就算已經有（比對規則跟匯入履歷共用，
  見 core.names），寧可少補一筆，也不要多出一筆重複的。
- 只送清單上列出來的欄位。我的資料有一百多項，這裡明列每一區用哪幾項，
  身分證字號、家人這類資料根本不在任何一區的清單裡。
- Cake 的「新增」不是彈出視窗，是在該區塊底下展開一張表單；按「建立」後表單收起來、
  那一筆出現在列表裡。必填欄沒填時表單會留在原地並標紅。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..core import names
from . import dom

log = logging.getLogger(__name__)

NAME = "cake"
LABEL = "Cake"
PROFILE_URL = "https://www.cake.me/dashboard/profile"
# 未登入時 Cake 會導去 /users/sign-in——注意是連字號。只列 signin／sign_in 會漏掉它，
# 然後把登入頁誤判成已登入（雛型踩過一次）
AUTH_WORDS = ("login", "signin", "sign-in", "sign_in", "signup", "sign-up", "sign_up",
              "users/sign")

# 每一區：區塊標題、我的資料裡的哪一串、用哪一項判斷「已經有了」
SECTIONS = (("experience", "工作經驗", "company"),
            ("education", "學歷", "school"),
            ("certificate", "資格認證", "name"))

CURRENT_RE = re.compile(r"至今|迄今|現在|目前|在職|present|now", re.I)
PERMANENT_RE = re.compile(r"永久|無期限|終身|不限期")
GENERIC_DEGREE_RE = re.compile(r"(副學士|學士|碩士|博士)學位")

# 找出「編輯／新增」入口，在 DOM 上做記號，之後用選擇器點它
ENTRIES_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('button,[role=button]').forEach(el => {
    if (!vis(el)) return;
    const t = clean(el.innerText);
    if (!/^(編輯|新增)$/.test(t)) return;
    let sec = '', n = el;
    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
      const h = n.querySelector?.('h1,h2,h3,h4');
      if (h && vis(h)) { sec = clean(h.innerText).slice(0, 20); break; }
    }
    if (!el.dataset.webEntry) el.dataset.webEntry = 'e' + out.length;
    out.push({text: t, section: sec, selector: `[data-web-entry="${el.dataset.webEntry}"]`});
  });
  return out;
}
"""

# 每一區目前印著哪些字：頁面文字一行一行看，從區塊標題那一行到下一個「同一層」的標題之前。
# 只認同一層：每一筆經歷的職稱也可能是標題標籤，拿它當結尾會把公司名稱切掉，
# 已經有的那一筆就被當成沒有、重複新增。找不到標題的區塊回 null——頁面改版了，那一區就不要動它
SECTION_LINES_JS = r"""
(titles) => {
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const lines = document.body.innerText.split('\n').map(clean).filter(Boolean);
  const hs = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
  const out = {};
  for (const t of titles) {
    const h = hs.find(x => clean(x.innerText) === t);
    const i = lines.indexOf(t);
    if (!h || i < 0) { out[t] = null; continue; }
    const stops = new Set(hs.filter(x => x.tagName === h.tagName)
      .map(x => clean(x.innerText)).filter(Boolean));
    const rest = [];
    for (let j = i + 1; j < lines.length && !stops.has(lines[j]); j++) rest.push(lines[j]);
    out[t] = rest;
  }
  return out;
}
"""

VISIBLE_PASSWORD_JS = """() => Array.from(document.querySelectorAll('input[type=password]'))
    .some(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"""


# ── 清單（純函式，不碰瀏覽器）────────────────────────────────────────────

def _field(key: str, label: str, kind: str, value: Any, required: bool = False,
           **extra: Any) -> Dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "value": str(value or "").strip(),
            "required": required, **extra}


def fmt_date(text: str, with_month: bool = True) -> str:
    """清單上顯示的日期：「2018/08」或「2018」。讀不出來就照原文，讓使用者自己改。"""
    year, month = dom.parse_ym(text or "")
    if year is None:
        return (text or "").strip()
    if not with_month:
        return str(year)
    return f"{year}/{month:02d}" if month else str(year)


def _level(text: str) -> Optional[int]:
    """學位的程度。「副學士」裡也有「學士」兩個字，要先攔下來。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    if "副學士" in t or "associate" in t:
        return 3
    return names.degree_level(t)


def map_degree(degree: str, options: List[str]) -> str:
    """我的資料的學位（學士、碩士、專科…）對到 Cake 的選項。

    Cake 用的是西式名稱（文學士（BA）、理學士（BS）、工學學士（BEng）…），另外有「學士學位」
    「碩士學位」這種通稱。同一個程度只有一個選項、或有通稱時才自動選；博士分成 PhD、MD、JD，
    選哪個要看主修，那是猜的——留給使用者挑。
    """
    opts = [o for o in options if o not in ("", "請選擇")]
    if degree in opts:
        return degree
    level = _level(degree)
    if level is None:
        return ""
    same = [o for o in opts if _level(o) == level]
    if len(same) == 1:
        return same[0]
    generic = [o for o in same if GENERIC_DEGREE_RE.fullmatch(o)]
    return generic[0] if len(generic) == 1 else ""


def on_page(name: str, lines: List[str]) -> bool:
    """這個名稱是不是已經印在那一區裡。簡稱、全半形、公司後綴都不算差別。

    反方向（那一行是名稱的一部分）只收四個字以上的行，「新增」「資訊」這種短字
    不能讓一整筆被當成已經有了。
    """
    key = names.name_key(name)
    if len(key) < 2:
        return False
    for line in lines:
        other = names.name_key(line)
        if key in other or (len(other) >= 4 and other in key):
            return True
    return False


def _experience(row: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    end = str(row.get("end") or "")
    current = bool(CURRENT_RE.search(end))
    return (f"{row.get('company') or ''}／{row.get('title') or ''}".strip("／"), [
        _field("company", "公司名稱", "text", row.get("company"), True),
        _field("title", "職稱", "text", row.get("title"), True),
        _field("start", "開始日期", "ym", fmt_date(str(row.get("start") or "")), True),
        _field("end", "結束日期", "ym", "" if current else fmt_date(end), True,
               alt="現任職位", alt_on=current),
        _field("description", "工作內容", "textarea", row.get("description")),
    ])


def _education(row: Dict[str, Any], degree_options: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
    degree = str(row.get("degree") or "")
    if degree_options:
        degree_field = _field("degree", "學歷", "select", map_degree(degree, degree_options),
                              True, options=[o for o in degree_options if o not in ("", "請選擇")])
    else:                          # 讀不到 Cake 的選項：照原文，送出時再找最接近的
        degree_field = _field("degree", "學歷", "text", degree, True)
    return (f"{row.get('school') or ''}／{degree}".strip("／"), [
        _field("school", "學校", "text", row.get("school"), True),
        degree_field,
        _field("department", "主修", "text", row.get("department"), True),
        _field("start", "開始日期", "y", fmt_date(str(row.get("start") or ""), False)),
        _field("end", "結束日期（或預期）", "y", fmt_date(str(row.get("end") or ""), False)),
    ])


def _certificate(row: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    expires = str(row.get("expires") or "")
    permanent = bool(PERMANENT_RE.search(expires))
    return (str(row.get("name") or ""), [
        _field("name", "名稱", "text", row.get("name"), True),
        _field("issuer", "發照機構", "text", row.get("issuer"), True),
        _field("issued", "發照日期", "ym", fmt_date(str(row.get("issued") or "")), True),
        _field("expires", "到期日", "ym", "" if permanent else fmt_date(expires), True,
               alt="永久有效", alt_on=permanent),
    ])


def problems(fields: List[Dict[str, Any]]) -> List[str]:
    """還缺哪幾欄（欄位名稱）。有勾「現任職位」「永久有效」的日期不必填。"""
    out = []
    for f in fields:
        value = str(f.get("value") or "").strip()
        if f.get("alt") and f.get("alt_on"):
            continue
        if f["kind"] in ("ym", "y"):
            if not value and not f["required"]:
                continue
            year, month = dom.parse_ym(value)
            if year is None or (f["kind"] == "ym" and month is None):
                out.append(f["label"])
        elif f["kind"] == "select":
            if (f["required"] or value) and value not in f.get("options", []):
                out.append(f["label"])
        elif f["required"] and not value:
            out.append(f["label"])
    return out


def build_plan(profile: Dict[str, Any], existing: Dict[str, Optional[List[str]]],
               degree_options: List[str]) -> List[Dict[str, Any]]:
    """依我的資料排出要補哪幾筆。existing 是 Cake 上每一區印著的字（None＝頁面上找不到那一區）。"""
    items: List[Dict[str, Any]] = []
    for root, section, name_key in SECTIONS:
        lines = existing.get(section)
        if lines is None:
            continue
        for i, row in enumerate(profile.get(root) or []):
            if not isinstance(row, dict) or not str(row.get(name_key) or "").strip():
                continue
            if root == "experience":
                title, fields = _experience(row)
            elif root == "education":
                title, fields = _education(row, degree_options)
            else:
                title, fields = _certificate(row)
            items.append({"id": f"{root}-{i}", "root": root, "section": section,
                          "title": title, "name_key": name_key,
                          "exists": on_page(str(row[name_key]), lines),
                          "fields": fields, "missing": problems(fields)})
    return items


# ── 瀏覽器上的動作 ──────────────────────────────────────────────────────

def in_login_flow(url: str, host: str = "cake.me") -> bool:
    """使用者正在登入（Cake 的登入頁，或 Google、Facebook 的授權頁）。這時候絕不能動他的頁面，
    一 goto 就把登入流程打斷了。"""
    url = url.lower()
    return host not in url or any(w in url for w in AUTH_WORDS)


class CakeSite:
    name = NAME
    label = LABEL

    def __init__(self, start_url: str = PROFILE_URL) -> None:
        self.start_url = start_url
        # www.cake.me 與 cake.me 都算（測試時換成本機的假網站）
        self.host = urlparse(start_url).netloc.lower().removeprefix("www.")

    async def goto_profile(self, page: Any) -> None:
        await page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass                     # 有些追蹤程式永遠不停，等不到就算了
        await page.wait_for_timeout(1500)

    problems = staticmethod(problems)

    async def check_login(self, page: Any) -> bool:
        """等登入時每幾秒問一次。使用者還在登入頁（或 Google 的授權頁）就不動他的頁面；
        不在個人檔案頁就帶過去看看——登入後 Cake 常常把人導到首頁。"""
        if in_login_flow(page.url, self.host):
            return False
        if "/dashboard" not in page.url.lower():
            await self.goto_profile(page)
        return await self.logged_in(page)

    async def logged_in(self, page: Any) -> bool:
        """三個條件都要成立：人在 /dashboard、網址不是登入頁、畫面上看不到密碼欄。
        最後一條才是關鍵——光看網址會被登入頁騙過去。"""
        url = page.url.lower()
        if "/dashboard" not in url or any(w in url for w in AUTH_WORDS):
            return False
        return not await page.evaluate(VISIBLE_PASSWORD_JS)

    async def read(self, page: Any,
                   profile: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
        """讀 Cake 上已經有什麼，排出清單。回傳（清單, 要提醒使用者的話）。"""
        await self.goto_profile(page)
        titles = [s[1] for s in SECTIONS]
        existing = await page.evaluate(SECTION_LINES_JS, titles)
        notes = [f"Cake 的頁面上找不到「{t}」這一區，這一區先不處理" for t in titles
                 if existing.get(t) is None]
        degree_options: List[str] = []
        wants_school = any(not on_page(str(r.get("school") or ""), existing.get("學歷") or [])
                           for r in profile.get("education") or [] if isinstance(r, dict))
        if existing.get("學歷") is not None and wants_school:
            degree_options = await self._degree_options(page)
        items = build_plan(profile, existing, degree_options)
        log.info("Cake 清單 新增=%d 已經有=%d 區塊缺=%d",
                 sum(not i["exists"] for i in items), sum(i["exists"] for i in items), len(notes))
        return items, notes

    async def _degree_options(self, page: Any) -> List[str]:
        """學歷的選項要打開表單才看得到：開「新增」、讀下拉、取消。全程不按建立。"""
        entry = await self._entry(page, "學歷")
        if not entry:
            return []
        try:
            await page.click(entry, timeout=10000)
            await page.wait_for_timeout(1500)
            got = await dom.locate(page, "學歷")
            return got["options"] if got and got["tag"] == "select" else []
        except Exception as e:
            log.warning("讀不到學歷選項 %s", type(e).__name__)
            return []
        finally:
            await dom.cancel(page)

    async def _entry(self, page: Any, section: str) -> Optional[str]:
        """那一區的「新增」按鈕。剛取消的表單收起來之後按鈕要重畫，找不到就捲過去再找。"""
        for _ in range(3):
            entries = await page.evaluate(ENTRIES_JS)
            hit = next((e for e in entries
                        if e["section"].startswith(section) and e["text"] == "新增"), None)
            if hit:
                return hit["selector"]
            try:
                await page.get_by_role("heading", name=section).first \
                    .scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)
        return None

    async def apply(self, page: Any, item: Dict[str, Any], save: bool = True) -> Tuple[bool, str]:
        """新增一筆：打開表單、逐欄填、按建立、回頭確認列表裡真的多了這一筆。"""
        if "/dashboard" not in page.url:
            await self.goto_profile(page)
        entry = await self._entry(page, item["section"])
        if not entry:
            return False, f"找不到「{item['section']}」的新增按鈕"
        await page.click(entry, timeout=10000)
        await page.wait_for_timeout(1500)

        # 勾選框先處理：它是唯一要真的點下去的，公司名稱打完字跳出的建議清單可能正好蓋在上面
        # （其他欄位是直接設值，被蓋住也不影響）
        for f in sorted(item["fields"], key=lambda f: not (f.get("alt") and f.get("alt_on"))):
            if f.get("alt") and f.get("alt_on"):
                ok, why = await dom.tick(page, f["alt"])
            elif not f["value"]:
                continue
            elif f["kind"] in ("ym", "y"):
                ok, why = await dom.put_ym(page, f["label"], f["value"], f["kind"] == "ym")
            else:
                ok, why = await dom.put(page, f["label"], f["value"])
            if not ok and f["required"]:
                await dom.cancel(page)
                return False, why
            if not ok:
                log.info("選填欄沒填上 item=%s：%s", item["id"], why)

        if not save:
            await dom.cancel(page)
            return True, ""
        ok, why = await dom.submit(page)
        if not ok:
            # 網站自己說了原因就照它說的，不要再猜「多半是必填欄沒填」
            errors = await dom.errors_on_page(page)
            await dom.cancel(page)
            return False, f"Cake 沒有收下這一筆：{'、'.join(errors)}" if errors else why
        await page.wait_for_timeout(1000)
        existing = await page.evaluate(SECTION_LINES_JS, [item["section"]])
        name = next(f["value"] for f in item["fields"] if f["key"] == item["name_key"])
        if not on_page(name, existing.get(item["section"]) or []):
            return False, "送出了，但列表裡還沒看到這一筆，請到 Cake 上確認"
        return True, ""

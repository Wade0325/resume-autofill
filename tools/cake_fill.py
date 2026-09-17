# -*- coding: utf-8 -*-
"""第三條填寫路線的雛型：把「我的資料」填進網頁表單（先以 Cake 個人檔案為對象）。

    .venv\\Scripts\\python.exe tools\\cake_fill.py login   # 開瀏覽器，你自己登入（狀態留在專用設定檔）
    .venv\\Scripts\\python.exe tools\\cake_fill.py recon   # 只讀：列出頁面上的欄位與可加開的區塊
    .venv\\Scripts\\python.exe tools\\cake_fill.py fill    # 只填不存

跟前兩條路線的關係：docx 那邊每一格有 `t0.r13.c1` 這種穩定位址，靠 `cells()` 列出來、
`apply_fills()` 寫回去。網頁沒有格子，對應物是 DOM 節點，所以這裡要做的是同一件事的
另一個接頭——`survey()` 相當於 `cells()`，`fill_one()` 相當於 `apply_fills()`，
中間「模型只挑一項資料」那一層將來可以原封不動接上。

**密碼不經過這支程式**：`login` 只是把瀏覽器開著等你自己登入，登入狀態留在專用的
瀏覽器設定檔 `data/cake_profile/`（`/data/` 不進版控），不是你平常那個 Chrome 設定檔。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright        # noqa: E402

import cake_web                                        # noqa: E402

PROFILE_URL = "https://www.cake.me/dashboard/profile"
# 專用的瀏覽器設定檔（不是你平常那個 Chrome 設定檔）。登入一次就一直有效：
# SPA 的 localStorage、service worker 都留在裡面，比只存 cookie 可靠。
USER_DIR = ROOT / "data" / "cake_profile"
OUT = ROOT / "data" / "cake"

# 這幾項一律不往求職平台送。身分證字號不該出現在公開的個人檔案上；
# 家人、緊急聯絡人、推薦人是「別人的資料」，表格沒問就不給——跟 filler 同一條原則。
BLOCKED_ROOTS = ("family", "reference", "emergency", "declaration")
BLOCKED_KEYS = {"basic.national_id", "basic.name_passport", "basic.blood_type",
                "basic.height", "basic.weight", "basic.health",
                "basic.military_exempt_reason", "basic.identity_category"}


def profile_fields() -> Dict[str, str]:
    """從產品資料庫拿「我的資料」，攤平成跟 filler 一樣的項目代碼。"""
    from backend.core import filler
    raw = sqlite3.connect(ROOT / "data" / "app.db").execute(
        "select value from kv where key='profile'").fetchone()
    if not raw:
        raise SystemExit("data/app.db 裡沒有 profile")
    fields = filler.fields_of(json.loads(raw[0]))
    return {k: v for k, v in fields.items()
            if k not in BLOCKED_KEYS and not k.startswith(BLOCKED_ROOTS)}


# ── 瀏覽器 ──────────────────────────────────────────────────────────────────

# Cake 未登入時導去 https://www.cake.me/users/sign-in ——注意是連字號。
# 只列 signin/sign_in 會漏掉它，然後把登入頁誤判成已登入（踩過一次）
AUTH_WORDS = ("login", "signin", "sign-in", "sign_in",
              "signup", "sign-up", "sign_up", "users/sign")


def open_browser(pw, headed: bool = True):
    """用系統已安裝的 Chrome ＋ 專用設定檔開一個瀏覽器，回傳 context。

    用 launch_persistent_context 而不是 launch()＋storage_state：Cake 是 SPA，
    登入狀態不只在 cookie 裡，換成持久設定檔才不會每次都要重登。
    """
    USER_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(USER_DIR), channel="chrome", headless=not headed,
        viewport={"width": 1440, "height": 1000}, locale="zh-TW",
        args=["--disable-blink-features=AutomationControlled"])


VISIBLE_PASSWORD_JS = """() => Array.from(document.querySelectorAll('input[type=password]'))
    .some(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })"""


def logged_in(page, navigate: bool = False) -> bool:
    """是不是真的登入了。

    三個條件都要成立：人在 /dashboard、網址不是登入頁、畫面上看不到密碼欄。
    最後一條才是關鍵——光看網址會被 /users/sign-in 騙過去。
    """
    if navigate and "/dashboard" not in page.url:
        try:
            page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            return False
    url = page.url.lower()
    if "/dashboard" not in url or any(w in url for w in AUTH_WORDS):
        return False
    # 不要等 <main>：Cake 的個人檔案頁根本沒有那個元素（也踩過一次）。
    # 等網路靜下來就好，真正的判準是密碼欄
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    return not page.evaluate(VISIBLE_PASSWORD_JS)


def cmd_login() -> int:
    """開著瀏覽器等你登入。登入狀態留在專用設定檔裡，之後的指令直接沿用。"""
    OUT.mkdir(parents=True, exist_ok=True)
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    with sync_playwright() as pw:
        ctx = open_browser(pw, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        if logged_in(page):
            print("這個設定檔已經是登入狀態，不用再登一次。")
            ctx.close()
            return 0
        print(f"瀏覽器已開啟，請在那個視窗登入 Cake（最多等 {minutes} 分鐘）。")
        print("（密碼不會經過這支程式，我也看不到；用 Google／Facebook 登入也可以）")
        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            time.sleep(4)
            url = page.url.lower()
            # 人在第三方登入頁（Google、Facebook…）時絕對不能動他的頁面，
            # 一 goto 就把登入流程打斷了
            if "cake.me" not in url:
                continue
            if any(w in url for w in AUTH_WORDS):
                continue
            if logged_in(page, navigate=True):
                print(f"登入完成，狀態留在 {USER_DIR}")
                ctx.close()
                return 0
        print(f"等了 {minutes} 分鐘還沒登入完成。重跑 login 再試一次（設定檔已保留）。")
        ctx.close()
        return 1


# ── 偵查：頁面上有哪些可寫的位置 ─────────────────────────────────────────────

SURVEY_JS = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && l.innerText.trim()) return l.innerText.trim();
    }
    const wrap = el.closest('label');
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    // 往上找最近的容器，取裡面第一段不是輸入框的文字
    let n = el.parentElement;
    for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
      const t = Array.from(n.childNodes)
        .filter(c => c.nodeType === 3).map(c => c.textContent.trim())
        .filter(Boolean).join(' ');
      if (t) return t.slice(0, 60);
    }
    return '';
  };
  const sectionOf = el => {
    let n = el;
    while (n) {
      const prev = n.previousElementSibling;
      if (prev) {
        const h = prev.matches('h1,h2,h3,h4') ? prev : prev.querySelector('h1,h2,h3,h4');
        if (h && h.innerText.trim()) return h.innerText.trim().slice(0, 40);
      }
      n = n.parentElement;
    }
    return '';
  };
  const path = el => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 6) {
      let p = n.tagName.toLowerCase();
      if (n.parentElement) {
        const sibs = Array.from(n.parentElement.children).filter(c => c.tagName === n.tagName);
        if (sibs.length > 1) p += `:nth-of-type(${sibs.indexOf(n) + 1})`;
      }
      parts.unshift(p);
      n = n.parentElement;
    }
    return parts.join(' > ');
  };

  const fields = [];
  document.querySelectorAll('input, textarea, select, [contenteditable="true"]').forEach(el => {
    if (!vis(el)) return;
    const type = (el.getAttribute('type') || el.tagName.toLowerCase()).toLowerCase();
    if (['hidden', 'submit', 'button', 'image'].includes(type)) return;
    fields.push({
      tag: el.tagName.toLowerCase(), type,
      name: el.getAttribute('name') || '', id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      label: labelOf(el), section: sectionOf(el),
      value: (el.value || el.innerText || '').slice(0, 40),
      selector: path(el),
    });
  });

  const adders = [];
  document.querySelectorAll('button, a, [role="button"]').forEach(el => {
    if (!vis(el)) return;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
    if (!t || t.length > 24) return;
    if (/新增|加入|添加|Add|\+/i.test(t)) {
      adders.push({text: t, section: sectionOf(el), selector: path(el)});
    }
  });

  const headings = Array.from(document.querySelectorAll('h1,h2,h3,h4'))
    .filter(vis).map(h => h.innerText.trim()).filter(Boolean).slice(0, 40);

  return {url: location.href, title: document.title, fields, adders, headings};
}
"""

# 個人檔案頁本身只是「檢視」——整頁只有一個可見輸入欄。真正的表單在點「編輯」
# 或「新增」之後的對話框裡，所以盤點欄位一定要先把那些對話框打開。
ENTRIES_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const path = el => {
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 8) {
      let p = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(`#${CSS.escape(n.id)}`); break; }
      if (n.parentElement) {
        const sibs = Array.from(n.parentElement.children).filter(c => c.tagName === n.tagName);
        if (sibs.length > 1) p += `:nth-of-type(${sibs.indexOf(n) + 1})`;
      }
      parts.unshift(p);
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const out = [];
  document.querySelectorAll('button,[role="button"]').forEach(el => {
    if (!vis(el)) return;
    const t = clean(el.innerText) || clean(el.getAttribute('aria-label'));
    if (!t || t.length > 12) return;
    if (!/^(編輯|新增)/.test(t)) return;
    let sec = '', n = el;
    for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
      const h = n.querySelector?.('h1,h2,h3,h4');
      if (h && vis(h)) { sec = clean(h.innerText).slice(0, 24); break; }
    }
    out.push({text: t, section: sec, selector: path(el)});
  });
  return out;
}
"""


def survey(page) -> Dict[str, Any]:
    """列出這一頁的可寫位置與「新增區塊」的按鈕。相當於 docx 那邊的 cells()。"""
    return page.evaluate(SURVEY_JS)


def cmd_recon() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = open_browser(pw, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        if not logged_in(page):
            print("還沒登入。請先跑：.venv\\Scripts\\python.exe tools\\cake_fill.py login")
            ctx.close()
            return 1
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        time.sleep(3)                     # SPA 還在渲染

        page.screenshot(path=str(OUT / "profile.png"), full_page=True)
        entries = page.evaluate(ENTRIES_JS)
        print(f"網址：{page.url}")
        print(f"入口（編輯／新增）共 {len(entries)} 個：")
        for e in entries:
            print(f"  「{e['text']}」 區塊={e['section']}")

        report: Dict[str, Any] = {"url": page.url, "entries": entries, "dialogs": []}
        for i, e in enumerate(entries, 1):
            name = f"{e['section']}｜{e['text']}"
            print(f"\n── {i}/{len(entries)} 打開「{name}」 ──")
            try:
                d = open_and_survey(page, e, OUT / f"dialog{i:02d}.png")
            except Exception as ex:
                print(f"   打不開或關不掉：{type(ex).__name__} {str(ex)[:80]}")
                close_dialog(page)
                continue
            report["dialogs"].append({"entry": e, **d})
            print(f"   欄位 {len(d['fields'])} 個")
            for f in d["fields"]:
                print(f"     [{f['type']:<9}] label={f['label'][:22]:<22} "
                      f"ph={f['placeholder'][:20]:<20} 值={f['value'][:18]}")

        (OUT / "survey.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n截圖：{OUT}\\dialogNN.png\n明細：{OUT / 'survey.json'}")
        ctx.close()
    return 0


def close_dialog(page) -> None:
    """把對話框關掉，而且絕不按儲存。先試 Esc，再找取消／關閉。"""
    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
    for name in ("取消", "關閉", "Cancel", "Close"):
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=3000)
                page.wait_for_timeout(600)
                return
        except Exception:
            pass


def open_and_survey(page, entry: Dict[str, Any], shot: Path) -> Dict[str, Any]:
    """點開一個「編輯／新增」入口，盤點裡面的欄位，再關掉。全程不按儲存。"""
    before = len(survey(page)["fields"])
    page.click(entry["selector"], timeout=10000)
    page.wait_for_timeout(2500)
    data = survey(page)
    # 對話框開了才有意義：欄位變多的那些就是這個對話框帶進來的
    data["fields"] = data["fields"][before:] if len(data["fields"]) > before else data["fields"]
    page.screenshot(path=str(shot))
    close_dialog(page)
    return {"fields": data["fields"], "shot": shot.name}


# Cake 的「學歷」下拉是西式學位名稱（副學士學位／文學士（BA）／工學學士（BEng）…），
# app.db 存的是台灣的說法。只對映沒有第二種答案的那幾個：專科系統就是 associate degree。
# 「大學」要看主修才知道是 BA 還是 BEng，會變成猜的，所以不對映——留白讓使用者自己選。
DEGREE = {"專科": "副學士學位", "二專": "副學士學位", "五專": "副學士學位",
          "副學士": "副學士學位"}


def build_plan(profile: Dict[str, Any], already: str) -> List[Dict[str, Any]]:
    """依手上的資料排出「要加開哪些區塊、各填什麼」。

    `already` 是個人檔案頁目前印出來的全部文字——名稱已經在上面的就不再加開，
    免得重複（Cake 上已經有奇偶科技、宜果國際與勤益科技大學）。
    """
    plan: List[Dict[str, Any]] = []

    for exp in profile.get("experience", []):
        name = (exp.get("company") or "").strip()
        if not name or name in already:
            continue
        plan.append({"section": "工作經驗", "entry": "新增", "title": f"{name}／{exp.get('title')}",
                     "fields": [("公司名稱", name, "text"),
                                ("職稱", exp.get("title"), "text"),
                                ("開始日期", exp.get("start"), "ym"),
                                ("結束日期", exp.get("end"), "ym"),
                                ("工作內容", exp.get("description"), "text")]})

    for edu in profile.get("education", []):
        name = (edu.get("school") or "").strip()
        if not name or name in already:
            continue
        fields = [("學校", name, "text"),
                  ("學歷", DEGREE.get((edu.get("degree") or "").strip()), "select"),
                  ("主修", edu.get("department"), "text"),
                  ("開始日期", edu.get("start"), "y"),
                  ("結束日期（或預期）", edu.get("end"), "y")]
        plan.append({"section": "學歷", "entry": "新增", "title": f"{name}／{edu.get('degree')}",
                     "fields": [f for f in fields if f[1]]})

    for cert in profile.get("certificate", []):
        name = (cert.get("name") or "").strip()
        if not name or name in already:
            continue
        plan.append({"section": "資格認證", "entry": "新增", "title": name,
                     "fields": [("名稱", name, "text"),
                                ("發照機構", cert.get("issuer"), "text")]})

    year_salary = (profile.get("job", {}) or {}).get("expected_salary_year", "")
    if year_salary:
        plan.append({"section": "求職偏好", "entry": "編輯", "title": f"期望年薪 {year_salary}",
                     "fields": [("最低期望年薪", str(year_salary).replace(",", ""), "text")]})
    return plan


def cmd_plan() -> int:
    """只看計畫，不開瀏覽器（把「已經在 Cake 上」那一關當成全都不在）。"""
    import sqlite3
    raw = sqlite3.connect(ROOT / "data" / "app.db").execute(
        "select value from kv where key='profile'").fetchone()
    for step in build_plan(json.loads(raw[0]), ""):
        print(f"[{step['section']}／{step['entry']}] {step['title']}")
        for label, value, kind in step["fields"]:
            print(f"    {label:<16} <- {str(value)[:40]}  ({kind})")
    return 0


def cmd_fill() -> int:
    """只填不存：把該加開的區塊打開、填進去、截圖，然後取消。"""
    import sqlite3
    OUT.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(ROOT / "data" / "app.db").execute(
        "select value from kv where key='profile'").fetchone()
    profile = json.loads(raw[0])

    with sync_playwright() as pw:
        ctx = open_browser(pw, headed=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=60000)
        if not logged_in(page):
            print("還沒登入。請先跑：.venv\\Scripts\\python.exe tools\\cake_fill.py login")
            ctx.close()
            return 1
        page.wait_for_timeout(2000)

        already = page.evaluate("() => document.body.innerText")
        plan = build_plan(profile, already)
        print(f"要處理 {len(plan)} 項（已經在 Cake 上的自動略過）\n")

        for i, step in enumerate(plan, 1):
            print(f"── {i}/{len(plan)} [{step['section']}／{step['entry']}] {step['title']}")
            # 取消上一筆之後那顆「新增」要重新畫出來，找不到就捲到那一區再試一次
            hit = None
            for attempt in range(3):
                entries = page.evaluate(cake_web.ENTRIES_JS)
                hit = next((e for e in entries
                            if e["section"].startswith(step["section"])
                            and e["text"] == step["entry"]), None)
                if hit:
                    break
                try:
                    page.get_by_role("heading", name=step["section"]).first \
                        .scroll_into_view_if_needed(timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
            if not hit:
                print(f"   × 找不到「{step['section']}」的「{step['entry']}」入口")
                continue
            try:
                page.click(hit["selector"], timeout=10000)
            except Exception as e:                       # noqa: BLE001
                print(f"   × 點不開：{type(e).__name__}")
                continue
            page.wait_for_timeout(2000)

            for label, value, kind in step["fields"]:
                if kind == "ym":
                    for line in cake_web.put_ym(page, label, str(value or "")):
                        print(line)
                elif kind == "y":
                    for line in cake_web.put_ym(page, label, str(value or ""), with_month=False):
                        print(line)
                else:
                    print(cake_web.put(page, label, value))

            shot = OUT / f"fill{i:02d}.png"
            page.screenshot(path=str(shot))
            print(f"   截圖：{shot.name}（沒有按建立／儲存）")
            cake_web.cancel(page)
            page.wait_for_timeout(1200)

        print("\n全部只填不存，Cake 上的資料完全沒有變動。")
        ctx.close()
    return 0


def cmd_fields() -> int:
    """只看資料：這次打算填哪些項目（不開瀏覽器）。"""
    fields = profile_fields()
    print(f"可用的項目 {len(fields)} 項（已排除身分證字號與別人的資料）：")
    for k, v in fields.items():
        print(f"  {k:<34} = {str(v)[:44]}")
    return 0


COMMANDS = {"login": cmd_login, "recon": cmd_recon, "fields": cmd_fields,
            "plan": cmd_plan, "fill": cmd_fill}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[cmd]())

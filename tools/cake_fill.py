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
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright        # noqa: E402

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

AUTH_WORDS = ("login", "signin", "signup", "sign_up", "sign_in")


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


def logged_in(page) -> bool:
    """現在這一頁是不是已經在個人檔案頁（而不是被踢回登入頁）。"""
    if any(w in page.url for w in AUTH_WORDS):
        return False
    try:
        page.wait_for_selector("main, [role='main']", timeout=8000)
        return True
    except Exception:
        return False


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
        print("（密碼不會經過這支程式，我也看不到）")
        deadline = time.time() + minutes * 60
        while time.time() < deadline:
            time.sleep(4)
            # 還停在登入頁就繼續等。不在登入頁時才回個人檔案頁確認，
            # 免得你正在打字就被我換頁
            if any(w in page.url for w in AUTH_WORDS):
                continue
            try:
                page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                continue
            if logged_in(page):
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

        data = survey(page)
        page.screenshot(path=str(OUT / "profile.png"), full_page=True)
        (OUT / "survey.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

        print(f"網址：{data['url']}")
        print(f"標題：{data['title']}")
        print(f"\n頁面標題（{len(data['headings'])}）：")
        for h in data["headings"]:
            print(f"  {h}")
        print(f"\n可寫欄位（{len(data['fields'])}）：")
        for f in data["fields"]:
            print(f"  [{f['type']:<10}] 區塊={f['section'][:16]:<16} "
                  f"label={f['label'][:24]:<24} ph={f['placeholder'][:18]:<18} "
                  f"已有值={f['value'][:16]}")
        print(f"\n可加開的區塊（{len(data['adders'])}）：")
        for a in data["adders"]:
            print(f"  「{a['text']}」 區塊={a['section'][:20]}")
        print(f"\n截圖：{OUT / 'profile.png'}\n明細：{OUT / 'survey.json'}")
        ctx.close()
    return 0


def cmd_fields() -> int:
    """只看資料：這次打算填哪些項目（不開瀏覽器）。"""
    fields = profile_fields()
    print(f"可用的項目 {len(fields)} 項（已排除身分證字號與別人的資料）：")
    for k, v in fields.items():
        print(f"  {k:<34} = {str(v)[:44]}")
    return 0


COMMANDS = {"login": cmd_login, "recon": cmd_recon, "fields": cmd_fields}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in COMMANDS:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[cmd]())

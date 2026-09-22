# -*- coding: utf-8 -*-
"""網頁表單的「位置」與「寫入」：docx 那邊的 cells() 與 apply_fills() 在網頁上的對應。

docx 每一格有 `t0.r13.c1` 這種位址；網頁的位址是 DOM 節點，靠「印在旁邊的那句話」找到它——
跟 filler 的「欄名取左邊最近一格」是同一個想法，只是網頁常常真的用 `<label for>` 綁好了。

回傳的原因只寫欄位名稱、不寫值：這些原因會進開發者 log。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
          "七月", "八月", "九月", "十月", "十一月", "十二月"]

_YM = re.compile(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})")
_Y = re.compile(r"(\d{4})")
_PLACEHOLDER_OPTIONS = {"", "請選擇"}


def parse_ym(text: str) -> Tuple[Optional[int], Optional[int]]:
    """「2023 年 7 月」「2021/10」「2013年09月01日」都要讀得出來。

    我的資料裡的日期寫法從來沒一致過（filler 那邊也吃過這個虧），一律用正規表示式抓，
    不假設某一種寫法。月份不在 1～12 就當作沒有。
    """
    if not text:
        return None, None
    m = _YM.search(text)
    if m:
        month = int(m.group(2))
        return int(m.group(1)), month if 1 <= month <= 12 else None
    m = _Y.search(text)
    return (int(m.group(1)), None) if m else (None, None)


# 給一段印在畫面上的話，找出它對應的第 index 個控制項
LOCATE_JS = r"""
([label, index]) => {
  const vis = el => { const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const CTRL = 'input, select, textarea';
  const mark = el => {
    if (!el.dataset.webfill) el.dataset.webfill = 'wf' + Math.random().toString(36).slice(2, 9);
    return `[data-webfill="${el.dataset.webfill}"]`;
  };
  const describe = (el, count) => ({
    selector: mark(el), tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(), count,
    options: el.tagName === 'SELECT'
      ? Array.from(el.options).map(o => clean(o.textContent)) : [],
  });

  // 路徑〇：控制項自己就帶著這個名字。Cake 的年薪欄畫面上只印「您期望的年薪區間是？」，
  // 「最低期望年薪」是 aria-label——不看這個就永遠找不到它
  const direct = Array.from(document.querySelectorAll(CTRL)).filter(vis).filter(el =>
    clean(el.getAttribute('aria-label')) === label ||
    clean(el.getAttribute('placeholder')) === label);
  if (direct.length) return describe(direct[Math.min(index, direct.length - 1)], direct.length);

  // 路徑一：有一個元素的文字剛好就是這句話（<label>、<span> 之類），必填的星號不算
  let cands = Array.from(document.querySelectorAll('label, span, div, p, h4'))
    .filter(vis)
    .filter(el => {
      const t = clean(el.innerText);
      if (!t) return false;
      return t.split('*')[0].trim() === label || t === label;
    })
    .filter(el => !el.querySelector(CTRL) || el.tagName === 'LABEL');

  // 路徑二：那句話是直接掛在容器裡的文字節點，沒有自己的元素
  if (!cands.length) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (clean(n.textContent).split('*')[0].trim() !== label) continue;
      const host = n.parentElement;
      if (host && vis(host)) { cands = [host]; break; }
    }
  }
  if (!cands.length) return null;

  const poolFor = lab => {
    let pool = [];
    if (lab.tagName === 'LABEL' && lab.htmlFor) {
      const el = document.getElementById(lab.htmlFor);
      if (el) pool = [el];
    }
    if (!pool.length) pool = Array.from(lab.querySelectorAll(CTRL)).filter(vis);
    if (!pool.length) {
      let n = lab.parentElement;
      for (let i = 0; i < 5 && n && !pool.length; i++, n = n.parentElement) {
        pool = Array.from(n.querySelectorAll(CTRL)).filter(vis).filter(c =>
          lab.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING);
      }
    }
    return pool;
  };

  // 同一句話可能印在好幾個地方——Cake 的「學歷」既是區塊標題也是欄位名稱。
  // 用人看表單的方式判斷：欄位名稱就緊貼在它那一欄上面，區塊標題離得遠。
  // 取「標籤底邊到控制項頂邊」最短的那一個，<label> 再加分。
  const scored = cands.map(lab => {
    const pool = poolFor(lab);
    if (!pool.length) return null;
    const gap = pool[0].getBoundingClientRect().top - lab.getBoundingClientRect().bottom;
    const bias = lab.tagName === 'LABEL' ? -1000 : 0;
    return {lab, pool, score: bias + (gap < -8 ? 1e6 : Math.abs(gap))};
  }).filter(Boolean);
  if (!scored.length) return null;
  scored.sort((a, b) => a.score - b.score);
  const pool = scored[0].pool;
  return describe(pool[Math.min(index, pool.length - 1)], pool.length);
}
"""

# 勾選框現在是不是勾著。Cake 的「現任職位」「永久有效」是樣式化的元件，
# 真正的 <input> 藏在外層；沒有 <input> 就看 aria-checked，都沒有回 null（不知道）
CHECKED_JS = r"""
el => {
  for (let n = el, i = 0; n && i < 4; n = n.parentElement, i++) {
    if (n.matches && n.matches('input[type=checkbox]')) return n.checked;
    const box = n.querySelector && n.querySelector('input[type=checkbox]');
    if (box) return box.checked;
    const aria = n.getAttribute && n.getAttribute('aria-checked');
    if (aria) return aria === 'true';
  }
  return null;
}
"""

ERRORS_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const out = new Set();
  document.querySelectorAll('[class*="error"],[class*="Error"],[class*="text-red"],[role=alert]')
    .forEach(el => {
      if (!vis(el)) return;
      const t = clean(el.innerText);
      if (t && t.length < 60) out.add(t);
    });
  return [...out].slice(0, 8);
}"""


async def locate(page: Any, label: str, index: int = 0) -> Optional[Dict[str, Any]]:
    return await page.evaluate(LOCATE_JS, [label, index])


def pick_option(options: List[str], value: str) -> Optional[str]:
    """下拉選單要選哪一項：字一樣的優先，其次互相包含的。對不上就不選——
    寧可留白，也不要填一個看起來對、其實是猜的值（跟 filler 的原則一致）。"""
    opts = [o for o in options if o not in _PLACEHOLDER_OPTIONS]
    exact = next((o for o in opts if o == value), None)
    if exact is not None:
        return exact
    return next((o for o in opts if value and (value in o or o in value)), None)


async def put(page: Any, label: str, value: str, index: int = 0) -> Tuple[bool, str]:
    """把值寫進「印著 label 的那一欄」。回傳（成功與否, 原因）。"""
    got = await locate(page, label, index)
    if not got:
        return False, f"找不到「{label}」這一欄"
    try:
        if got["tag"] == "select":
            pick = pick_option(got["options"], value)
            if pick is None:
                return False, f"「{label}」的選項裡沒有對得上的"
            await page.select_option(got["selector"], label=pick)
        else:
            await page.fill(got["selector"], value)
        return True, ""
    except Exception as e:
        log.warning("寫入失敗 label=%s %s", label, type(e).__name__)
        return False, f"「{label}」寫不進去"


async def put_ym(page: Any, label: str, text: str, with_month: bool = True) -> Tuple[bool, str]:
    """日期是「年」「月」兩個下拉（學歷那邊只有年）。"""
    year, month = parse_ym(text)
    if year is None:
        return False, f"「{label}」讀不出年份"
    ok, why = await put(page, label, str(year), index=0)
    if not ok or not with_month:
        return ok, why
    if month is None:
        return False, f"「{label}」缺月份"
    return await put(page, label, MONTHS[month - 1], index=1)


async def tick(page: Any, label: str) -> Tuple[bool, str]:
    """把「現任職位」「永久有效」這種勾選框勾起來（已經勾著就不動）。

    Cake 的勾選框是樣式化的元件，`page.check()` 會失敗，所以點印著字的那一塊。
    """
    loc = page.get_by_text(label, exact=True)
    shown = [loc.nth(i) for i in range(await loc.count()) if await loc.nth(i).is_visible()]
    if not shown:
        return False, f"找不到「{label}」"
    target = shown[-1]
    if await target.evaluate(CHECKED_JS) is True:
        return True, ""
    try:
        await target.click(timeout=5000)
        await page.wait_for_timeout(300)
    except Exception as e:
        log.warning("勾選失敗 label=%s %s", label, type(e).__name__)
        return False, f"「{label}」勾不起來"
    if await target.evaluate(CHECKED_JS) is False:
        return False, f"「{label}」勾不起來"
    return True, ""


async def submit(page: Any, names: Tuple[str, ...] = ("建立", "儲存")) -> Tuple[bool, str]:
    """按下送出，並確認表單真的收起來了。

    必填欄沒填時網站會把表單留在原地並標紅，那不算成功。
    """
    for name in names:
        btn = page.get_by_role("button", name=name, exact=True)
        for i in range(await btn.count()):
            one = btn.nth(i)
            if not (await one.is_visible() and await one.is_enabled()):
                continue
            try:
                await one.click(timeout=5000)
                await page.wait_for_timeout(2500)
                if await one.is_visible():          # 還在原地＝沒送出去
                    return False, "表單沒送出去，多半是有必填欄沒填"
                return True, ""
            except Exception as e:
                log.warning("送出失敗 button=%s %s", name, type(e).__name__)
                return False, f"按「{name}」失敗"
    return False, "找不到送出的按鈕"


async def errors_on_page(page: Any) -> List[str]:
    """畫面上紅色的錯誤訊息。送不出去時要知道卡在哪（這些是網站印的字，不是我的資料）。"""
    try:
        return await page.evaluate(ERRORS_JS)
    except Exception:
        return []


async def cancel(page: Any) -> None:
    """關掉目前開著的表單，絕不按建立／儲存。"""
    btn = page.get_by_role("button", name="取消")
    for i in range(await btn.count()):
        try:
            if await btn.nth(i).is_visible():
                await btn.nth(i).click(timeout=3000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass
    for _ in range(2):
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

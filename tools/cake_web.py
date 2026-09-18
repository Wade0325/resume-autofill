# -*- coding: utf-8 -*-
"""網頁表單這一側的「位置」與「寫入」——相當於 docx 那條路的 cells() 與 apply_fills()。

docx 每一格有 `t0.r13.c1` 這種位址；網頁的位址是 DOM 節點，靠「印在旁邊的那句話」
找到它——跟 filler 的「欄名取左邊最近一格」是同一個想法，只是網頁常常真的用
`<label for>` 綁好了，找起來更穩。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月",
          "七月", "八月", "九月", "十月", "十一月", "十二月"]

_YM = re.compile(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})")
_Y = re.compile(r"(\d{4})")


def parse_ym(text: str) -> Tuple[Optional[int], Optional[int]]:
    """「2023 年 7 月」「2021/10」「2013年09月」都要讀得出來。

    app.db 存的日期格式從來沒一致過（filler 那邊也吃過這個虧），所以這裡一律
    用正規表示式抓，而不是假設某一種寫法。
    """
    if not text:
        return None, None
    m = _YM.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _Y.search(text)
    return (int(m.group(1)), None) if m else (None, None)


# 找出「編輯／新增」入口，順便在 DOM 上做記號，之後用選擇器點它
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
    if (!el.dataset.cakeEntry) el.dataset.cakeEntry = 'e' + out.length;
    out.push({text: t, section: sec, selector: `[data-cake-entry="${el.dataset.cakeEntry}"]`});
  });
  return out;
}
"""

# 給一段印在畫面上的話，找出它對應的第 index 個控制項
LOCATE_JS = r"""
([label, index]) => {
  const vis = el => { const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden'; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const CTRL = 'input, select, textarea';
  const mark = el => {
    if (!el.dataset.cakefill) el.dataset.cakefill = 'cf' + Math.random().toString(36).slice(2, 9);
    return `[data-cakefill="${el.dataset.cakefill}"]`;
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

  // 路徑一：有一個元素的文字剛好就是這句話（<label>、<span> 之類）
  let cands = Array.from(document.querySelectorAll('label, span, div, p, h4'))
    .filter(vis)
    .filter(el => {
      const t = clean(el.innerText);
      if (!t) return false;
      return t.split('*')[0].trim() === label || t === label;
    })
    .filter(el => !el.querySelector(CTRL) || el.tagName === 'LABEL');

  // 路徑二：那句話是直接掛在容器裡的文字節點，沒有自己的元素
  // （Cake 的「最低期望年薪」就是這樣，只找元素會漏掉）
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
    return {lab, pool, score: (lab.tagName === 'LABEL' ? -1000 : 0) + (gap < -8 ? 1e6 : Math.abs(gap))};
  }).filter(Boolean);
  if (!scored.length) return null;
  scored.sort((a, b) => a.score - b.score);
  const pool = scored[0].pool;
  return describe(pool[Math.min(index, pool.length - 1)], pool.length);
}
"""


def locate(page, label: str, index: int = 0) -> Optional[Dict[str, Any]]:
    return page.evaluate(LOCATE_JS, [label, index])


def put(page, label: str, value: str, index: int = 0) -> str:
    """把一個值寫進「印著 label 的那一欄」。回傳一句話說明結果。

    下拉選單用意思相符的選項，對不上就不選——寧可留白，不要填一個看起來對、
    其實是猜的值（跟 filler 的原則一致）。
    """
    if value in (None, "", "無"):
        return f"  {label}：資料是空的，略過"
    got = locate(page, label, index)
    if not got:
        return f"  {label}：× 找不到這一欄"
    sel = got["selector"]
    try:
        if got["tag"] == "select":
            opts = [o for o in got["options"] if o and o != "請選擇"]
            pick = next((o for o in opts if o == value), None)
            if pick is None:
                pick = next((o for o in opts if value in o or o in value), None)
            if pick is None:
                return f"  {label}：× 選項裡沒有「{value}」（有 {opts[:6]}…）"
            page.select_option(sel, label=pick)
            return f"  {label}：選了「{pick}」"
        page.fill(sel, str(value))
        return f"  {label}：填入「{str(value)[:30]}」"
    except Exception as e:                       # noqa: BLE001 - 要把原因帶回報告
        return f"  {label}：× 寫入失敗 {type(e).__name__} {str(e)[:60]}"


def put_ym(page, label: str, text: str, with_month: bool = True) -> List[str]:
    """日期是「年」「月」兩個下拉。學歷那邊只有年。"""
    year, month = parse_ym(text)
    out = []
    if year is None:
        return [f"  {label}：× 讀不出年份（原文「{text}」）"]
    out.append(put(page, label, str(year), index=0))
    if with_month:
        if month is None:
            out.append(f"  {label}[月]：原文沒有月份，留白")
        else:
            out.append(put(page, label, MONTHS[month - 1], index=1))
    return out


def submit(page) -> Tuple[bool, str]:
    """真的按下送出。回傳（成功與否, 說明）。

    新增的按鈕寫「建立」，編輯的寫「儲存」。按完要確認表單真的收起來了——
    必填欄沒填時 Cake 會把表單留在原地並標紅，那不算成功。
    """
    for name in ("建立", "儲存", "Create", "Save"):
        try:
            btn = page.get_by_role("button", name=name, exact=True)
        except Exception:
            continue
        for i in range(btn.count()):
            try:
                one = btn.nth(i)
                if not (one.is_visible() and one.is_enabled()):
                    continue
                one.click(timeout=5000)
                page.wait_for_timeout(3000)
                if one.is_visible():        # 還在原地＝沒送出去
                    return False, f"按了「{name}」但表單沒收起來（多半是必填欄沒填）"
                return True, f"按了「{name}」"
            except Exception as e:          # noqa: BLE001
                return False, f"按「{name}」失敗：{type(e).__name__} {str(e)[:60]}"
    return False, "找不到建立／儲存按鈕"


def errors_on_page(page) -> List[str]:
    """把畫面上紅色的錯誤訊息撈出來，送不出去時要知道卡在哪。"""
    return page.evaluate(r"""() => {
      const vis = el => { const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0; };
      const clean = t => (t || '').replace(/\s+/g, ' ').trim();
      const out = new Set();
      document.querySelectorAll('[class*="error"],[class*="Error"],[class*="text-red"],[role=alert]')
        .forEach(el => { if (vis(el)) { const t = clean(el.innerText); if (t && t.length < 60) out.add(t); } });
      return [...out].slice(0, 8);
    }""")


def tick(page, label: str) -> str:
    """勾一個核取方塊（「永久有效」「現任職位」這種）。

    注意 Cake 的「永久有效」不是真的 <input type=checkbox>，是樣式化的元件，
    page.check() 會失敗。要勾它得改點外層那個可點擊的元素——還沒做。
    """
    got = locate(page, label)
    if not got:
        return f"  {label}：× 找不到這個勾選框"
    try:
        page.check(got["selector"], timeout=5000)
        return f"  {label}：已勾選"
    except Exception as e:                  # noqa: BLE001
        return f"  {label}：× 勾不起來 {type(e).__name__}"


def cancel(page) -> None:
    """關掉目前開著的表單，絕不按建立／儲存。"""
    for name in ("取消", "Cancel"):
        try:
            btn = page.get_by_role("button", name=name)
            for i in range(btn.count()):
                if btn.nth(i).is_visible():
                    btn.nth(i).click(timeout=3000)
                    page.wait_for_timeout(800)
                    return
        except Exception:
            pass
    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

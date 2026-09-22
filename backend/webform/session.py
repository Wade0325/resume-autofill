# -*- coding: utf-8 -*-
"""一個平台的網頁填寫流程：開瀏覽器 → 等使用者登入 → 讀清單 → 使用者確認 → 一筆一筆存。

狀態只在瀏覽器執行緒上改，API 那邊用 `snapshot()` 讀一份拷貝。同一時間只做一件事，
正在做的時候再叫別的會收到 Busy。

階段（stage）：
- closed   瀏覽器沒開（或使用者把它關了）
- login    等使用者在瀏覽器視窗登入
- reading  讀平台上已經有的資料、排清單
- ready    清單排好了，等使用者確認
- running  正在一筆一筆存
"""
from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .. import actions
from .browser import Browser, BrowserError

log = logging.getLogger(__name__)

LOGIN_WAIT_SECONDS = 30 * 60
LOGIN_POLL_SECONDS = 3.0


class Busy(Exception):
    """正在做別的事。"""


class Session:
    def __init__(self, site: Any, browser: Browser,
                 profile: Callable[[], Dict[str, Any]]) -> None:
        self.site = site
        self.browser = browser
        self.profile = profile           # 每次讀清單時才去拿：使用者可能剛改過我的資料
        self._lock = threading.Lock()
        self._task: Optional[Any] = None
        self._state: Dict[str, Any] = self._blank("closed")

    @staticmethod
    def _blank(stage: str, message: str = "") -> Dict[str, Any]:
        return {"stage": stage, "message": message, "items": [], "notes": [],
                "progress": {"done": 0, "total": 0, "current": ""}, "results": []}

    # ── 給 API 呼叫（任何執行緒）──────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self._state)
        # 使用者自己把視窗關了：清單留著，但要重新開啟才能做事
        state["browser_open"] = self.browser.is_open
        state["site"] = {"name": self.site.name, "label": self.site.label}
        return state

    def open(self) -> None:
        self._start(self._open(), **self._blank("login", "正在開啟瀏覽器…"))

    def refresh(self) -> None:
        self._start(self._refresh(), stage="reading",
                    message=f"正在讀取你在 {self.site.label} 上的資料…")

    def run(self, picks: List[Dict[str, Any]], save: bool = True) -> None:
        """picks：[{id, values: {key: 值}, alts: {key: 勾不勾}}]。

        先驗過再開始：缺欄位丟 ValueError，訊息直接給使用者看。
        """
        with self._lock:
            if self._state["stage"] not in ("ready",):
                raise Busy("清單還沒準備好")
            plan = {i["id"]: i for i in self._state["items"]}
        todo = []
        for pick in picks:
            item = copy.deepcopy(plan.get(str(pick.get("id"))))
            if item is None or item["exists"]:
                raise ValueError("清單已經變了，請重新讀取一次")
            values, alts = pick.get("values") or {}, pick.get("alts") or {}
            for f in item["fields"]:
                if f["key"] in values:
                    f["value"] = str(values[f["key"]] or "").strip()
                if f.get("alt") and f["key"] in alts:
                    f["alt_on"] = bool(alts[f["key"]])
            missing = self.site.problems(item["fields"])
            if missing:
                raise ValueError(f"「{item['title']}」還缺：{'、'.join(missing)}")
            todo.append(item)
        if not todo:
            raise ValueError("沒有勾選任何一筆")
        self._start(self._run(todo, save), stage="running", message="", results=[],
                    progress={"done": 0, "total": len(todo), "current": ""})

    def close(self) -> None:
        with self._lock:
            task = self._task
        if task is not None and not task.done():
            task.cancel()                # 會一路取消到瀏覽器執行緒上那個 task
        self.browser.run(self.browser.close(), timeout=30)
        self._set(**self._blank("closed"))

    def _start(self, coro: Any, **stage: Any) -> None:
        """開始一件事。階段在這裡就先換好：API 馬上回傳的那一份狀態已經是新的，
        前端看到忙碌中才會開始輪詢（等工作真的開跑才換，前端可能先看到舊的階段就停了）。"""
        with self._lock:
            if self._task is not None and not self._task.done():
                coro.close()
                raise Busy("正在處理中，請稍候")
            self._state.update(stage)
            self._task = self.browser.submit(self._guard(coro))

    async def _guard(self, coro: Any) -> None:
        """每件事的最外層：沒預料到的錯誤不能讓狀態卡在「忙碌中」，前端會一直轉圈圈。"""
        try:
            await coro
        except Exception as e:
            log.warning("%s 流程出錯 %s", self.site.name, type(e).__name__, exc_info=True)
            with self._lock:
                results = self._state.get("results") or []
            self._set(**{**self._blank("closed" if not self.browser.is_open else "ready",
                                       "出了點問題，請再試一次"), "results": results})

    # ── 以下在瀏覽器執行緒上 ───────────────────────────────────────────

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state.update(kw)

    def _progress(self, **kw: Any) -> None:
        with self._lock:
            self._state["progress"].update(kw)

    async def _open(self) -> None:
        try:
            page = await self.browser.page()
            await page.bring_to_front()
            await self.site.goto_profile(page)
        except BrowserError as e:
            self._set(**self._blank("closed", str(e)))
            return
        except Exception as e:
            log.warning("開啟 %s 失敗 %s", self.site.name, type(e).__name__)
            self._set(**self._blank("closed", f"開不了 {self.site.label}，請確認網路連線"))
            return
        try:
            ready = await self.site.logged_in(page)
        except Exception:                    # 頁面還在跳轉：當作還沒登入，交給等登入那一段
            ready = False
        if not ready:
            self._set(message=f"請在跳出來的瀏覽器視窗登入 {self.site.label}")
            if not await self._wait_login(page):
                return
            actions.record("登入 %s 成功", self.site.label)
        await self._read(page)

    async def _wait_login(self, page: Any) -> bool:
        deadline = time.monotonic() + LOGIN_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(LOGIN_POLL_SECONDS)
            if page.is_closed() or not self.browser.is_open:
                self._set(**self._blank("closed", "瀏覽器視窗關掉了，要繼續請重新開啟"))
                return False
            try:
                if await self.site.check_login(page):
                    return True
            except Exception as e:           # 頁面正在跳轉，下一輪再看
                log.debug("等登入時頁面還在跳 %s", type(e).__name__)
        self._set(**self._blank("closed", "等太久還沒登入，要繼續請重新開啟"))
        return False

    async def _refresh(self) -> None:
        try:
            page = await self.browser.page()
        except BrowserError as e:
            self._set(**self._blank("closed", str(e)))
            return
        await self._read(page)

    async def _read(self, page: Any, results: Optional[List[Dict[str, Any]]] = None) -> None:
        self._set(stage="reading", message=f"正在讀取你在 {self.site.label} 上的資料…")
        try:
            await self.site.goto_profile(page)
            if not await self.site.logged_in(page):
                self._set(stage="login", message=f"請在跳出來的瀏覽器視窗登入 {self.site.label}")
                if not await self._wait_login(page):
                    return
            items, notes = await self.site.read(page, self.profile())
        except Exception as e:
            log.warning("讀取 %s 失敗 %s", self.site.name, type(e).__name__, exc_info=True)
            self._set(**{**self._blank("closed" if not self.browser.is_open else "ready",
                                       f"讀取 {self.site.label} 的資料失敗，請再試一次"),
                         "results": results or []})     # 剛存完的結果不要跟著消失
            return
        self._set(stage="ready", message="", items=items, notes=notes,
                  results=results or [], progress={"done": 0, "total": 0, "current": ""})

    async def _run(self, todo: List[Dict[str, Any]], save: bool) -> None:
        results: List[Dict[str, Any]] = []
        page = None
        try:
            page = await self.browser.page()
            for n, item in enumerate(todo):
                self._progress(done=n, current=f"{item['section']}：{item['title']}")
                try:
                    ok, why = await self.site.apply(page, item, save=save)
                except Exception as e:
                    # 不帶 traceback：Playwright 的錯誤訊息可能夾著剛填進去的值
                    log.warning("新增失敗 item=%s %s", item["id"], type(e).__name__)
                    ok, why = False, "操作網頁時出錯了"
                    if page.is_closed():         # 使用者把視窗關了，剩下的不必一筆一筆再失敗一次
                        results.append({"id": item["id"], "section": item["section"],
                                        "title": item["title"], "ok": False,
                                        "why": "瀏覽器視窗關掉了"})
                        break
                log.info("%s item=%s ok=%s %s", self.site.name, item["id"], ok, why)
                if ok and save:
                    actions.record("在 %s 新增%s成功", self.site.label, item["section"])
                elif not ok:
                    actions.problem("在 %s 新增%s失敗：%s", self.site.label, item["section"], why)
                results.append({"id": item["id"], "section": item["section"],
                                "title": item["title"], "ok": ok, "why": why})
                self._set(results=list(results))
            self._progress(done=len(todo), current="")
        except BrowserError as e:
            self._set(**{**self._blank("closed", str(e)), "results": results})
            return
        except Exception as e:
            log.warning("%s 執行中斷 %s", self.site.name, type(e).__name__)
        if page is None or page.is_closed() or not self.browser.is_open:
            self._set(stage="closed", message="瀏覽器視窗關掉了，沒做完的要重新開啟再試",
                      results=results)
            return
        # 存完重新讀一次：剛存進去的那幾筆會變成「已經有」，清單跟 Cake 上一致
        await self._read(page, results)

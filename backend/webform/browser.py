# -*- coding: utf-8 -*-
"""網頁填寫用的專用瀏覽器。

- 用系統內建的 Edge（Windows 10／11 都有），沒有才用 Chrome。不用 Playwright 自己的
  Chromium：那要另外下載，等於叫使用者安裝東西。
- 專用的設定檔 `data/browser/`：登入狀態留在這裡，下次不必再登入；
  使用者平常用的瀏覽器一點都不碰。
- Playwright 的物件只能在建立它的事件迴圈裡用，所以整個瀏覽器住在一條專屬執行緒上，
  別的地方用 `submit()`／`run()` 把工作丟進去。那條執行緒自己開 ProactorEventLoop：
  `uvicorn --reload` 會把預設的迴圈換成 Selector，那種迴圈開不了子行程，瀏覽器根本起不來。
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Coroutine, Optional, Sequence

log = logging.getLogger(__name__)

CHANNELS = ("msedge", "chrome")


class BrowserError(Exception):
    """開不了瀏覽器。訊息是給使用者看的白話。"""


class Browser:
    def __init__(self, user_dir: Path, channels: Sequence[str] = CHANNELS,
                 executable: Optional[str] = None, headless: bool = False) -> None:
        self.user_dir = Path(user_dir)
        self.channels = tuple(channels)
        self.executable = executable        # 測試用：直接指定 chromium 執行檔
        self.headless = headless
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None

    # ── 從任何執行緒呼叫 ─────────────────────────────────────────────

    def submit(self, coro: Coroutine) -> Future:
        """把一段工作丟到瀏覽器執行緒上跑。"""
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    def run(self, coro: Coroutine, timeout: float = 60) -> Any:
        """丟進去並等結果。"""
        return self.submit(coro).result(timeout)

    @property
    def is_open(self) -> bool:
        return self._ctx is not None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = (asyncio.ProactorEventLoop() if sys.platform == "win32"
                        else asyncio.new_event_loop())
                threading.Thread(target=loop.run_forever, name="webform-browser",
                                 daemon=True).start()
                self._loop = loop
            return self._loop

    # ── 以下只能在瀏覽器執行緒上 await ─────────────────────────────────

    async def page(self) -> Any:
        """我們這一頁。視窗沒開就開一個；使用者把它關掉了就再開一頁。"""
        ctx = await self._context()
        if self._page is None or self._page.is_closed():
            alive = [p for p in ctx.pages if not p.is_closed()]
            self._page = alive[0] if alive else await ctx.new_page()
        return self._page

    async def close(self) -> None:
        ctx, self._ctx, self._page = self._ctx, None, None
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:                    # 使用者已經自己關掉了
                pass
        if self._pw is not None:
            pw, self._pw = self._pw, None
            await pw.stop()

    async def _context(self) -> Any:
        if self._ctx is not None:
            return self._ctx
        from playwright.async_api import async_playwright
        if self._pw is None:
            self._pw = await async_playwright().start()
        self.user_dir.mkdir(parents=True, exist_ok=True)
        # channel 給空字串＝Playwright 自己的 Chromium（只有測試會這樣用）
        tries = [{"executable_path": self.executable}] if self.executable else \
            [{"channel": c} if c else {} for c in self.channels]
        last: Optional[Exception] = None
        for extra in tries:
            try:
                # AutomationControlled：不然 navigator.webdriver 是 true，使用者按「用 Google 登入」
                # 會被 Google 以「這個瀏覽器可能不安全」擋下（登入是使用者自己在做的）
                ctx = await self._pw.chromium.launch_persistent_context(
                    str(self.user_dir), headless=self.headless, no_viewport=True,
                    locale="zh-TW", **extra,
                    args=["--window-size=1280,900",
                          "--disable-blink-features=AutomationControlled"])
                break
            except Exception as e:               # 這個瀏覽器沒裝，或設定檔被別的視窗占著
                log.info("瀏覽器開不起來 %s：%s", extra, str(e).splitlines()[0][:120])
                last = e
        else:
            if last is not None and "user data directory is already in use" in str(last):
                raise BrowserError("專用的瀏覽器視窗已經開著了，請先把它關掉再試一次")
            raise BrowserError("找不到 Edge 或 Chrome，網頁填寫需要其中一個")
        ctx.on("close", lambda _ctx: self._forget(ctx))
        self._ctx = ctx
        log.info("瀏覽器已開啟 %s", extra.get("channel") or "指定的執行檔")
        return ctx

    def _forget(self, ctx: Any) -> None:
        """使用者自己把視窗關了。下次要用時再開。"""
        if self._ctx is ctx:
            self._ctx, self._page = None, None
            log.info("瀏覽器視窗已關閉")

"""FastAPI 應用組裝。"""
from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, db
from .api import imports, jobs, logs, meta, models, profile
from .logging_setup import request_id_var, setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    db.init()
    # 使用者在介面上切換過模型的話，重啟後沿用那次的選擇
    saved_model = db.get_kv("llm_model")
    if saved_model:
        config.LLM_MODEL = saved_model
    purged = db.purge_old_jobs()
    if purged:
        log.info("已清除 %d 筆過期上傳檔（超過 %d 小時）", purged, config.JOB_RETENTION_HOURS)
    stale = db.fail_stale_jobs()
    if stale:
        log.info("標記 %d 筆被重啟中斷的分析為失敗", stale)
    log.info("服務啟動 http://%s:%d 　推論引擎 %s",
             config.API_HOST, config.API_PORT, config.LLM_HOST)
    yield
    log.info("服務關閉")


app = FastAPI(title="Resume AutoFill", version="0.1.0", lifespan=lifespan)

# 前端定期輪詢的端點：成功回應多到會洗版，降成 DEBUG；失敗仍照常記 WARNING。
# 除了固定路徑，分析期間每兩秒一次的進度查詢（GET /api/jobs/{id}、
# GET /api/imports/{id}）也算——上傳與完成事件另有自己的 log，不會因此消失
POLLED_PATHS = {"/api/models", "/api/logs"}
POLL_RE = re.compile(r"^/api/(jobs|imports)/[^/]+$")

# 服務只綁 127.0.0.1，但瀏覽器裡的任何網頁都連得到它。以下兩層擋的是「別的網站借你的瀏覽器」：
# 1. Host 只收本機名稱——DNS rebinding 的網頁把自己的網域解析到 127.0.0.1，
#    Host 標頭仍是它的網域，擋掉就讀不到 /api/profile
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """2. 會改東西的請求只收本機介面發的。

    multipart 上傳不觸發 CORS 預檢，任何網站都能叫瀏覽器往這裡送檔案、改資料。
    瀏覽器送這類請求一定帶 Origin，跟 Host 對不上就是別的網站發的。
    沒帶 Origin 的（curl、測試程式）不是瀏覽器，不會被別的網站借用，照常放行。
    開發時 Vite proxy 設成不改 Host（vite.config.ts 的 changeOrigin: false），
    頁面與 Host 都是 localhost:5177，一樣對得上。
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).netloc.lower() != request.headers.get(
                "host", "").lower():
            return JSONResponse(status_code=403, content={"detail": "只接受本機介面發出的請求"})
    return await call_next(request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """產生 request_id 串起同一次請求的所有 log，並記錄耗時。

    uvicorn 的 access log 已停用，改在這裡記，才帶得到 request_id。
    """
    rid = uuid.uuid4().hex[:8]
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    # reset 必須包住所有 log 呼叫，否則收尾那行會拿不到 request_id
    try:
        try:
            response = await call_next(request)
        except Exception:
            elapsed = int((time.perf_counter() - t0) * 1000)
            log.exception("未攔截例外 %s %s 耗時=%dms",
                          request.method, request.url.path, elapsed)
            # 把 request_id 回給使用者，回報問題時可以直接對到 log
            return JSONResponse(status_code=500,
                                content={"detail": "伺服器內部錯誤", "request_id": rid},
                                headers={"X-Request-Id": rid})

        elapsed = int((time.perf_counter() - t0) * 1000)
        if response.status_code >= 400:
            level = logging.WARNING
        elif request.url.path in POLLED_PATHS or (
                request.method == "GET" and POLL_RE.match(request.url.path)):
            level = logging.DEBUG
        else:
            level = logging.INFO
        log.log(level, "%s %s → %d 耗時=%dms",
                request.method, request.url.path, response.status_code, elapsed)
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        request_id_var.reset(token)


app.include_router(meta.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(imports.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(models.router, prefix="/api")

# 正式版把 build 好的前端交給同一個服務托管，使用者只會看到一個網址。
# 開發時 dist 不存在，走 Vite dev server 的 proxy，這裡就跳過。
# 打包版的目錄佈局刻意跟 repo 相同（app/backend + app/frontend/dist），這行兩邊通用
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
# 預覽把公司給的 .docx 渲染在介面本身的網頁裡，文件裡夾帶的東西等於跟介面同源。
# 前端已經拿掉 javascript: 連結；這是第二道：只准跑自己的腳本，行內腳本、javascript:
# 網址一律擋。style 要 'unsafe-inline'（docx-preview 插 <style>、React 的 style 屬性），
# blob:／data: 是 docx-preview 的圖片與字型、pdf.js 的 worker
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data: blob:; worker-src 'self' blob:; "
        "connect-src 'self' blob:; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'")
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")
    # dist 裡其他檔案（favicon.svg 這類）啟動時列好，網址只拿來查表、不拿來組路徑：
    # uvicorn 會把 %2f、%5c 解成斜線，「..%2f..%2fdata/app.db」組出來就跑出 dist，
    # 整份個人資料庫都拿得到；「%5C%5C主機%5C分享」光是查它存不存在，Windows 就會
    # 連到那台機器（SMB）交出登入雜湊——而任何網頁放一張 <img> 就能叫瀏覽器送這種請求
    _DIST_FILES = {p.relative_to(_DIST).as_posix(): p for p in _DIST.rglob("*")
                   if p.is_file() and p.relative_to(_DIST).parts[0] != "assets"}

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """react-router 的 /fill、/import 在伺服器上並不存在，一律回 index.html
        交給前端接手，否則直接輸入網址或按重整就會 404。"""
        if path.startswith("api/"):
            raise HTTPException(404, "找不到這個 API 端點")
        found = _DIST_FILES.get(path, _DIST / "index.html")
        return FileResponse(found, headers={"Content-Security-Policy": _CSP})

    log.info("前端靜態檔已掛載 path=%s", _DIST)

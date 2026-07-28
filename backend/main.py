"""FastAPI 應用組裝。"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import imports, jobs, meta, profile
from .logging_setup import request_id_var, setup_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(config.LOG_DIR, config.LOG_LEVEL)
    db.init()
    purged = db.purge_old_jobs()
    if purged:
        log.info("已清除 %d 筆過期上傳檔（超過 %d 小時）", purged, config.JOB_RETENTION_HOURS)
    log.info("服務啟動 http://%s:%d 　推論引擎 %s",
             config.API_HOST, config.API_PORT, config.LLM_HOST)
    yield
    log.info("服務關閉")


app = FastAPI(title="Resume AutoFill", version="0.1.0", lifespan=lifespan)


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
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
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

# 正式版把 build 好的前端交給同一個服務托管，使用者只會看到一個網址。
# 開發時 dist 不存在，走 Vite dev server 的 proxy，這裡就跳過。
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """react-router 的 /fill、/import 在伺服器上並不存在，一律回 index.html
        交給前端接手，否則直接輸入網址或按重整就會 404。"""
        if path.startswith("api/"):
            raise HTTPException(404, "找不到這個 API 端點")
        candidate = _DIST / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")

    log.info("前端靜態檔已掛載 path=%s", _DIST)

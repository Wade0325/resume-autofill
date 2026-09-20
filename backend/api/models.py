"""模型管理的 HTTP 端點。邏輯都在 model_manager，這裡只翻譯請求與錯誤。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import model_manager

router = APIRouter(prefix="/models", tags=["models"])


class SelectIn(BaseModel):
    name: str


class UrlIn(BaseModel):
    url: str


@router.get("")
def list_models() -> dict:
    return model_manager.status()


@router.post("/select")
def select_model(body: SelectIn) -> dict:
    try:
        model_manager.select(body.name)
    except model_manager.ModelError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True}


@router.delete("/{name}")
def delete_model(name: str) -> dict:
    """刪掉模型檔（含視覺投影檔與沒下載完的暫存檔）。正在用的要先切換到別顆。"""
    try:
        model_manager.delete(name)
    except model_manager.ModelError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True}


@router.post("/download")
def download_model(body: SelectIn) -> dict:
    try:
        model_manager.download(body.name)
    except model_manager.ModelError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True}


@router.post("/download-url")
def download_from_url(body: UrlIn) -> dict:
    try:
        name = model_manager.download_url(body.url)
    except model_manager.ModelError as e:
        raise HTTPException(e.status, str(e))
    return {"ok": True, "name": name}

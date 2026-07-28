"""個人資料與使用者設定。"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from .. import actions, db
from ..schemas import ProfileIn, SettingsIn

log = logging.getLogger(__name__)
router = APIRouter(tags=["profile"])


@router.get("/profile")
def get_profile() -> ProfileIn:
    return db.get_kv("profile") or {}


@router.put("/profile")
def put_profile(profile: ProfileIn) -> dict:
    db.put_kv("profile", profile)
    # 只記結構規模，不記內容——profile 裡全是個資
    log.info("個人資料已更新 top_level_keys=%d", len(profile))
    actions.record("修改欄位成功")
    return {"ok": True}


@router.get("/settings", response_model=SettingsIn)
def get_settings() -> SettingsIn:
    return SettingsIn(**db.get_settings())


@router.put("/settings", response_model=SettingsIn)
def put_settings(settings: SettingsIn) -> SettingsIn:
    db.put_kv("settings", settings.model_dump())
    log.info("設定已更新 %s", settings.model_dump())
    actions.record("修改設定成功")
    return settings

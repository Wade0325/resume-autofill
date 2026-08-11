"""個人資料與使用者設定。"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from .. import actions, db
from ..schemas import ProfileIn

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

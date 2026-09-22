"""個人資料與使用者設定。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Request, Response

from .. import actions, db, profiles
from ..schemas import ProfileIn, ProfileVersionOut

router = APIRouter(tags=["profile"])


@router.get("/profile")
def get_profile() -> ProfileIn:
    return db.get_kv("profile") or {}


@router.put("/profile")
def put_profile(profile: ProfileIn) -> dict:
    why = profiles.problem(profile)
    if why:
        raise HTTPException(422, why)
    profiles.save(profile, "save")
    actions.record("修改欄位成功")
    return {"ok": True}


@router.get("/profile/versions", response_model=List[ProfileVersionOut])
def list_versions() -> list:
    return profiles.versions()


@router.post("/profile/versions/{version_id}/restore")
def restore_version(version_id: int) -> ProfileIn:
    profile = profiles.restore_version(version_id)
    if profile is None:
        raise HTTPException(404, "找不到這個版本，可能已經被較新的版本擠掉了")
    return profile


@router.get("/profile/export")
def export_profile(request: Request) -> Response:
    """下載成 .json 備份檔；檔名帶日期，存了好幾份也分得出來。"""
    # 別的網站讀不到回應，但能叫瀏覽器連過來，把一份明文個資丟進使用者的「下載」資料夾。
    # 瀏覽器會標明請求從哪來：只收本機介面（same-origin）與直接打網址（none）
    if request.headers.get("sec-fetch-site", "none") not in ("same-origin", "none"):
        raise HTTPException(403, "只能從本機的操作介面匯出")
    day = datetime.now().strftime("%Y%m%d")
    body = json.dumps(profiles.export(), ensure_ascii=False, indent=2)
    disposition = (f'attachment; filename="profile-{day}.json"; '
                   f"filename*=UTF-8''{quote(f'我的資料-{day}.json')}")
    actions.record("匯出我的資料成功")
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": disposition})


@router.post("/profile/restore")
def restore_profile(payload: Any = Body(...)) -> ProfileIn:
    try:
        return profiles.restore_file(payload)
    except ValueError as e:
        raise HTTPException(422, f"這個檔案不能還原：{e}")

"""上傳收檔的共同驗證：填寫與匯入的規則相同，只差接受的副檔名。"""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

from .. import config

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def read_upload(file: UploadFile, exts: tuple[str, ...]) -> tuple[str, bytes]:
    name = file.filename or ""
    if not name.lower().endswith(exts):
        raise HTTPException(
            400, f"只接受 {' 或 '.join(exts)} 檔案。舊版 .doc 請先用 Word 另存成 .docx")
    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {config.MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限")
    if not content:
        raise HTTPException(400, "檔案是空的")
    return name, content

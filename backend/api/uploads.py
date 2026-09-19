"""填寫與匯入共用的驗證：上傳收檔（規則相同，只差接受的副檔名）與工作代碼。"""
from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Path, UploadFile

from .. import config

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 工作代碼一律是 service 發的 12 個十六進位字元，而它會接進檔案路徑：
# Windows 上網址裡的 %5C 解出來是反斜線，不先擋格式，「..\..\」就能讓路徑跑出 jobs 資料夾
WorkId = Annotated[str, Path(pattern=r"^[0-9a-f]{12}$")]


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

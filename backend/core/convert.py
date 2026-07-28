"""舊版 .doc → .docx 轉檔。

python-docx 只認得 OOXML；.doc（Word 97-2003 二進位格式）沒有可靠的
純 Python 解析器，所以借用本機 LibreOffice 的無頭模式轉檔。
找不到 LibreOffice 時丟出可以直接顯示給使用者的訊息。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class ConversionError(Exception):
    """轉檔失敗（含找不到 LibreOffice）。訊息可直接回給使用者。"""


# Windows 安裝器不會把 soffice 加進 PATH，得列出常見安裝位置
_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)


def find_soffice() -> Optional[str]:
    override = os.environ.get("RESUME_AUTOFILL_SOFFICE")
    if override:
        return override if Path(override).exists() else None
    return shutil.which("soffice") or next(
        (c for c in _CANDIDATES if Path(c).exists()), None)


def doc_to_docx(content: bytes) -> bytes:
    if not find_soffice():
        raise ConversionError(
            "轉換 .doc 需要 LibreOffice 但找不到。請安裝 LibreOffice，"
            "或先用 Word 另存成 .docx 再上傳")
    return _convert(content, "input.doc", "docx")


def docx_to_pdf(content: bytes) -> bytes:
    """左右對照預覽用：轉成 PDF 才能呈現真正的排版。"""
    if not find_soffice():
        raise ConversionError("產生排版預覽需要 LibreOffice 但找不到。請安裝 LibreOffice")
    return _convert(content, "input.docx", "pdf")


def _convert(content: bytes, src_name: str, target: str) -> bytes:
    soffice = find_soffice()
    with tempfile.TemporaryDirectory(prefix="convert_") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / src_name
        src.write_bytes(content)
        # 獨立的 profile 目錄：使用者若正開著 LibreOffice，
        # 共用 profile 會搶鎖導致轉檔靜默失敗
        profile = (tmp_dir / "profile").as_uri()
        try:
            proc = subprocess.run(
                [soffice, "--headless", "--norestore",
                 f"-env:UserInstallation={profile}",
                 "--convert-to", target, "--outdir", str(tmp_dir), str(src)],
                capture_output=True, text=True, errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            raise ConversionError("LibreOffice 轉檔逾時，請改用 Word 另存成 .docx")

        out = src.with_suffix(f".{target}")
        if proc.returncode != 0 or not out.exists():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ConversionError(f"LibreOffice 轉檔失敗：{detail or '未知原因'}")
        return out.read_bytes()

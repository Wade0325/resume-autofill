"""PDF 取字與頁面截圖，都用 pypdfium2。

PDF 本身就是排版結果，每個字的座標都寫在檔案裡，直接畫得出來。
.docx 沒有這個性質——它是「內容＋樣式規則」，要先有排版引擎去算，
所以這裡不處理 .docx：填寫與匯入的預覽都改由瀏覽器渲染，
判讀則走攤平文字（實測比截圖準，見 b8abaa8；空白表格的欄位清單
2026-08-17 再驗一次，結論相同——截圖會讓模型漏掉表格上真的有的欄位）。
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata

import pypdfium2 as pdfium

log = logging.getLogger(__name__)


def pdf_to_page_pngs(content: bytes, scale: float = 300 / 72) -> list[bytes]:
    """PDF 基準 72 DPI，scale 300/72 = 300 DPI。整份轉出，不限頁數。

    每頁截圖約吃 2.7k tokens：頁數多的文件會超出模型上下文，
    那次視覺呼叫會失敗，匯入流程接手退回純文字模式，不會卡死。
    """
    pdf = pdfium.PdfDocument(content)
    out: list[bytes] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            try:
                buf = io.BytesIO()
                bitmap.to_pil().save(buf, format="PNG")
                out.append(buf.getvalue())
            finally:
                # 常駐服務裡靠 GC 釋放會拖到行程結束，PDFium 資源要主動關
                bitmap.close()
                page.close()
    finally:
        pdf.close()
    return out


def _page_text(textpage) -> str:
    """依座標重排成閱讀順序：先由上而下分行，行內再由左而右。

    PDF 存的是繪製順序，不是閱讀順序——104 履歷就把「語言能力」「資格認證」
    這類區塊標題畫在自己的內容之後，照原順序取字會讓那些內容沒有標題可依附。
    charbox 取 loose（整個字框而非墨跡框），中文標點才不會被誤判成空格。
    """
    chars = []
    for i in range(textpage.count_chars()):
        char = textpage.get_text_range(i, 1)
        if not char or char in "\r\n":
            continue
        left, bottom, right, top = textpage.get_charbox(i, loose=True)
        if top > bottom:
            chars.append((bottom, top, left, right, char))
    chars.sort(key=lambda c: -(c[0] + c[1]))

    lines: list[list] = []
    for bottom, top, left, right, char in chars:
        prev = lines[-1] if lines else None
        if prev and min(top, prev[1]) - max(bottom, prev[0]) > (top - bottom) / 2:
            prev[0], prev[1] = min(prev[0], bottom), max(prev[1], top)
            prev[2].append((left, right, char))
        else:
            lines.append([bottom, top, [(left, right, char)]])

    out = []
    for *_, parts in lines:
        buf, edge = "", None
        for left, right, char in sorted(parts):
            if edge is not None and left - edge > (right - left) / 2:
                buf += " "
            buf, edge = buf + char, right if edge is None else max(edge, right)
        if line := re.sub(r" {2,}", " ", buf).strip():
            out.append(line)
    return "\n".join(out)


# NFKC 涵蓋康熙部首區（U+2F00–2FDF），但部首補充區（U+2E80–2EFF）沒有分解
# 對應，104 的字型一樣會把常用字對映過去（「民」存成 ⺠ U+2EA0、「長」存成
# ⻑ U+2ED1），逐字驗證與前端框選就會對不上。只轉「與獨立漢字同形」的部首；
# 偏旁專用形（亻氵扌…）不會單獨成字，不用轉。
# 前端 ResumeDocView 的 squash 有同一張表，兩邊要一起改。
_RADICALS = str.maketrans({
    "⺇": "几", "⺊": "卜", "⺒": "巳", "⺓": "幺",
    "⺝": "月", "⺞": "歹", "⺟": "母", "⺠": "民",
    "⺩": "王", "⺪": "疋", "⺬": "示", "⻁": "虎",
    "⻄": "西", "⻆": "角", "⻑": "長", "⻘": "青",
    "⻝": "食", "⻣": "骨", "⻤": "鬼", "⻱": "龜",
})


def pdf_to_text(content: bytes) -> str:
    """PDF 逐頁取字。

    一定要 NFKC＋部首對照：PDF 的字型常把中文對映到部首區（「工」存成
    U+2F2F ⼯），肉眼一樣但碼位不同。匯入會逐字驗證模型抽出的值有沒有
    出現在原文，不正規化的話每個含中文的值都會對不上而被丟掉。
    """
    pdf = pdfium.PdfDocument(content)
    try:
        pages = [_page_text(pdf[i].get_textpage()) for i in range(len(pdf))]
    finally:
        pdf.close()
    normalized = unicodedata.normalize("NFKC", "\n".join(pages))
    translated = normalized.translate(_RADICALS)
    radicals = sum(1 for a, b in zip(normalized, translated) if a != b)
    fixed = translated.strip()
    log.debug("pdf_to_text: pages=%d chars=%d radical_fixes=%d",
              len(pages), len(fixed), radicals)
    return fixed

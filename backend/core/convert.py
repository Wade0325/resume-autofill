"""PDF 取字與頁面截圖，都用 pypdfium2。

PDF 本身就是排版結果，每個字的座標都寫在檔案裡，直接畫得出來。
.docx 沒有這個性質——它是「內容＋樣式規則」，要先有排版引擎去算，
所以這裡不處理 .docx：填寫與匯入的預覽都改由瀏覽器渲染，
匯入判讀則走攤平文字（實測比截圖準）。
"""
from __future__ import annotations

import io
import unicodedata

import pypdfium2 as pdfium


def pdf_to_page_pngs(content: bytes, max_pages: int = 3, scale: float = 2.0) -> list[bytes]:
    """scale 2.0 約為 144 DPI，表格細字仍可辨識；頁數上限是為了守住
    模型的上下文長度（每頁截圖約吃 1~2k tokens）。
    """
    pdf = pdfium.PdfDocument(content)
    out: list[bytes] = []
    try:
        for i in range(min(len(pdf), max_pages)):
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


def pdf_to_text(content: bytes) -> str:
    """PDF 逐頁取字。

    一定要 NFKC：PDF 的字型常把中文對映到康熙部首區（「工」存成 U+2F2F ⼯），
    肉眼一樣但碼位不同。匯入會逐字驗證模型抽出的值有沒有出現在原文，
    不正規化的話每個含中文的值都會對不上而被丟掉。
    """
    pdf = pdfium.PdfDocument(content)
    try:
        pages = [pdf[i].get_textpage().get_text_bounded() for i in range(len(pdf))]
    finally:
        pdf.close()
    return unicodedata.normalize("NFKC", "\n".join(pages)).strip()

"""把前端的 favicon.svg 轉成啟動器用的 app.ico（exe 圖示與系統匣圖示）。

    python tools/make_icon.py

每個尺寸各用 Chromium 畫一次，不是畫一張大的再縮：16 px 那張直接縮會糊成一團。
圖示改了就改 frontend/public/favicon.svg，再跑這支——兩邊才會一致。
需要 playwright 與它的 chromium（跟瀏覽器測試同一套，`pip install -e ".[test]"`）。
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "frontend" / "public" / "favicon.svg"
ICO = ROOT / "launcher" / "ResumeAutoFill.Launcher" / "app.ico"
SIZES = [256, 128, 64, 48, 32, 24, 16]


def _chromium(pw):
    for kwargs in ({}, {"executable_path": str(
            Path.home() / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe")}):
        try:
            return pw.chromium.launch(**kwargs)
        except Exception:
            continue
    raise SystemExit("找不到可用的 chromium（跑 playwright install chromium）")


def main() -> None:
    svg = SVG.read_text(encoding="utf-8")
    images = []
    with sync_playwright() as pw:
        browser = _chromium(pw)
        for size in SIZES:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(
                "<html><body style='margin:0;background:transparent'>"
                f"<div style='width:{size}px;height:{size}px'>{svg}</div></body></html>")
            page.eval_on_selector("svg", f"e => {{ e.style.width='{size}px'; "
                                         f"e.style.height='{size}px'; e.style.display='block' }}")
            png = page.screenshot(omit_background=True)
            images.append(Image.open(io.BytesIO(png)).convert("RGBA"))
            page.close()
        browser.close()
    images[0].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[1:])
    print(f"已寫入 {ICO.relative_to(ROOT)}（{', '.join(map(str, SIZES))} px）")


if __name__ == "__main__":
    main()

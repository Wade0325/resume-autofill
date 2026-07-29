"""把履歷 docx 整份截成頁面 PNG（LibreOffice → PDF → 點陣圖）。

用法：
    python tools/snapshot.py [docx路徑] [輸出目錄]

不給參數時：拿 input/ 裡最新的 .docx，輸出到 input/imagesz/。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core import convert  # noqa: E402


def main() -> None:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        candidates = sorted((ROOT / "input").glob("*.docx"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            sys.exit("input/ 裡沒有 .docx 檔")
        src = candidates[0]

    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "input" / "imagesz"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"來源：{src}")
    pngs = convert.docx_to_page_pngs(src.read_bytes(), max_pages=99)
    for i, png in enumerate(pngs, 1):
        dest = out_dir / f"{src.stem}_p{i}.png"
        dest.write_bytes(png)
        print(f"  第 {i} 頁 → {dest}（{len(png) // 1024} KB）")
    print(f"完成，共 {len(pngs)} 頁")


if __name__ == "__main__":
    main()

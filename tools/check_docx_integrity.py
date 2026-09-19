"""比對原稿與填好的 .docx：填寫只該加字，不該弄掉原稿的東西。

    python tools\\check_docx_integrity.py 原稿.docx 填好.docx [原稿2.docx 填好2.docx ...]

研究迴圈的評分只比對每一格的文字，看不見這些：畫出來的方框勾選框、Wingdings 勾選框
（w:sym）、功能變數、文字方塊、底線，以及填進空格的字有沒有沿用原本的字型。
這支補上那一塊，檢查文件本體、頁首、頁尾：

- 結構元素（圖片、文字方塊、w:sym、功能變數、內容控制項、超連結…）只能多不能少
- 有底線文字的段落數只能多不能少——寫回時把底線抹掉，數字就會掉
- 「段落標記有字型、run 卻沒有字型設定」的 run 不能變多——新增的 run 沒帶格式，
  字就退回預設字型（原本標楷體的表格填出新細明體）
- 方框符號（□■☐☑…）總數只列出來參考：勾選是 □ 換 ■，總數本來就該不變

任何一項變少或變多 → 結束碼 1。
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from typing import Dict

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W,
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}
# 只能多不能少的元素。名稱是給人看的，值是 XPath
STRUCTURE = {
    "圖片 w:drawing": ".//w:drawing",
    "舊式圖形 w:pict": ".//w:pict",
    "內嵌物件 w:object": ".//w:object",
    "相容性區塊 mc:AlternateContent": ".//mc:AlternateContent",
    "文字方塊 w:txbxContent": ".//w:txbxContent",
    "符號字元 w:sym": ".//w:sym",
    "功能變數標記 w:fldChar": ".//w:fldChar",
    "功能變數指令 w:instrText": ".//w:instrText",
    "簡單功能變數 w:fldSimple": ".//w:fldSimple",
    "內容控制項 w:sdt": ".//w:sdt",
    "超連結 w:hyperlink": ".//w:hyperlink",
    "書籤 w:bookmarkStart": ".//w:bookmarkStart",
    "Tab w:tab（run 內）": ".//w:r/w:tab",
}
BOXES = "□■☐☑☒▢◻◼▣"
PART_RE = re.compile(r"word/(document|header\d*|footer\d*)\.xml$")


def _underlined(run) -> bool:
    u = run.find("w:rPr/w:u", NS)
    return u is not None and u.get(f"{{{W}}}val", "single") != "none"


def _text(run) -> str:
    return "".join(t.text or "" for t in run.findall("w:t", NS))


def measure(path: Path) -> Dict[str, int]:
    counts = {name: 0 for name in STRUCTURE}
    counts.update({"有底線文字的段落": 0, "段落有字型、run 沒字型": 0, "方框符號": 0})
    with zipfile.ZipFile(path) as zf:
        for part in sorted(n for n in zf.namelist() if PART_RE.search(n)):
            root = etree.fromstring(zf.read(part))
            for name, xpath in STRUCTURE.items():
                counts[name] += len(root.xpath(xpath, namespaces=NS))
            for p in root.iter(f"{{{W}}}p"):
                runs = p.xpath("./w:r | ./w:hyperlink/w:r", namespaces=NS)
                if any(_underlined(r) and _text(r) for r in runs):
                    counts["有底線文字的段落"] += 1
                if p.find("w:pPr/w:rPr", NS) is not None:
                    counts["段落有字型、run 沒字型"] += sum(
                        1 for r in runs if _text(r).strip() and r.find("w:rPr", NS) is None)
                counts["方框符號"] += sum(_text(r).count(ch) for r in runs for ch in BOXES)
    return counts


def compare(original: Path, filled: Path) -> bool:
    before, after = measure(original), measure(filled)
    ok = True
    print(f"== {original.name} → {filled.name}")
    for name in before:
        b, a = before[name], after[name]
        if name == "段落有字型、run 沒字型":
            bad = a > b
        elif name == "方框符號":
            bad = False                     # 只列出來參考
        else:
            bad = a < b
        ok &= not bad
        if bad or b or a:
            print(f"  {'壞了' if bad else '　　'} {name:24} {b:>4} → {a}")
    print("  結果：" + ("完整" if ok else "有東西不見或跑掉了"))
    return ok


def main(argv) -> int:
    for stream in (sys.stdout, sys.stderr):     # Windows 主控台預設 cp950，中文會炸
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not argv or len(argv) % 2:
        print(__doc__)
        return 2
    pairs = [(Path(argv[i]), Path(argv[i + 1])) for i in range(0, len(argv), 2)]
    results = [compare(o, f) for o, f in pairs]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

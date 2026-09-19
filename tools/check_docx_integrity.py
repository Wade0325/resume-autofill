"""比對原稿與填好的 .docx：填寫只該加字，不該弄掉原稿的東西。

    python tools\\check_docx_integrity.py 原稿.docx 填好.docx [原稿2.docx 填好2.docx ...]

研究迴圈的評分只比對每一格的文字，看不見這些：畫出來的方框勾選框、Wingdings 勾選框
（w:sym）、功能變數、文字方塊、底線、刪除線，以及字的格式有沒有被改掉。
這支補上那一塊，檢查文件本體、頁首、頁尾：

- 結構元素（圖片、文字方塊、w:sym、功能變數、內容控制項、超連結…）只能多不能少
- 有底線文字的段落數只能多不能少——寫回時把底線抹掉，數字就會掉
- 「段落標記有設字型（w:rFonts）、run 自己卻沒有」的 run 不能變多——新增的 run 沒帶格式，
  字就退回預設字型（原本標楷體的表格填出新細明體）。只看有沒有 rPr 不夠：
  預覽的黃底 run 有 rPr，裡面卻可能只有黃底
- 有刪除線或隱藏的文字 run 不能變多——空白格的段落標記可能帶著刪除線，新字照抄就被劃掉
- 原稿印好的字，格式一個都不能變：逐段把兩邊的字對齊（difflib），對得上的字比格式
  （黃底不算）。空白、底線、點線、方框本來就是留給人填、會被換掉的，不算
- 方框符號（□■☐☑…）總數只列出來參考：勾選是 □ 換 ■，總數本來就該不變

任何一項壞了 → 結束碼 1。只印數字，不印文件內容（填好的檔案裡是個資）。
"""
from __future__ import annotations

import copy
import re
import sys
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Tuple

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
# 留給人填、填寫時本來就會被換掉的字元：格式比對不看它們
FILLERS = set(" 　\t\n_＿.．…-—–" + BOXES)
PART_RE = re.compile(r"word/(document|header\d*|footer\d*)\.xml$")
MORE_IS_BAD = {"段落有字型、run 沒字型", "有刪除線或隱藏的文字 run"}


def _underlined(run) -> bool:
    u = run.find("w:rPr/w:u", NS)
    return u is not None and u.get(f"{{{W}}}val", "single") != "none"


def _on(run, tag: str) -> bool:
    el = run.find(f"w:rPr/w:{tag}", NS)
    return el is not None and el.get(f"{{{W}}}val", "true") not in ("0", "false", "none")


def _text(run) -> str:
    return "".join(t.text or "" for t in run.findall("w:t", NS))


def _no_font(run) -> bool:
    """run 自己沒設字型，也沒套字元樣式（樣式裡可能有字型）。"""
    return (run.find("w:rPr/w:rFonts", NS) is None
            and run.find("w:rPr/w:rStyle", NS) is None)


def _runs(p) -> List:
    return p.xpath("./w:r | ./w:hyperlink/w:r", namespaces=NS)


def _parts(path: Path) -> Dict[str, etree._Element]:
    with zipfile.ZipFile(path) as zf:
        return {n: etree.fromstring(zf.read(n)) for n in sorted(zf.namelist())
                if PART_RE.search(n)}


def measure(parts: Dict[str, etree._Element]) -> Dict[str, int]:
    counts = {name: 0 for name in STRUCTURE}
    counts.update({"有底線文字的段落": 0, "段落有字型、run 沒字型": 0,
                   "有刪除線或隱藏的文字 run": 0, "方框符號": 0})
    for root in parts.values():
        for name, xpath in STRUCTURE.items():
            counts[name] += len(root.xpath(xpath, namespaces=NS))
        for p in root.iter(f"{{{W}}}p"):
            runs = _runs(p)
            if any(_underlined(r) and _text(r) for r in runs):
                counts["有底線文字的段落"] += 1
            if p.find("w:pPr/w:rPr/w:rFonts", NS) is not None:
                counts["段落有字型、run 沒字型"] += sum(
                    1 for r in runs if _text(r).strip() and _no_font(r))
            counts["有刪除線或隱藏的文字 run"] += sum(
                1 for r in runs if _text(r).strip()
                and any(_on(r, t) for t in ("strike", "dstrike", "vanish")))
            counts["方框符號"] += sum(_text(r).count(ch) for r in runs for ch in BOXES)
    return counts


def _fingerprint(run) -> bytes:
    """run 的格式，黃底不算（網頁預覽會加）。"""
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        return b""
    rpr = copy.deepcopy(rpr)
    for el in rpr.findall("w:highlight", NS):
        rpr.remove(el)
    return etree.tostring(rpr, method="c14n", exclusive=True)


def _chars(p) -> List[Tuple[str, bytes]]:
    """段落的每個字與它的格式，組法跟 python-docx 的 para.text 一樣。"""
    out = []
    for r in _runs(p):
        fp = _fingerprint(r)
        for el in r:
            tag = etree.QName(el).localname
            if tag == "t":
                s = el.text or ""
            elif tag in ("tab", "ptab"):
                s = "\t"
            elif tag == "br":
                s = "\n" if el.get(f"{{{W}}}type") in (None, "textWrapping") else ""
            elif tag == "cr":
                s = "\n"
            elif tag == "noBreakHyphen":
                s = "-"
            else:
                continue
            out.extend((ch, fp) for ch in s)
    return out


def format_changes(before: Dict[str, etree._Element], after: Dict[str, etree._Element]) -> int:
    """原稿印好的字裡，格式被改掉的字數。段落數對不上就整份算壞（回 -1）。"""
    changed = 0
    for name, root in before.items():
        pa = list(root.iter(f"{{{W}}}p"))
        pb = list(after[name].iter(f"{{{W}}}p")) if name in after else []
        if len(pa) != len(pb):
            return -1
        for a, b in zip(pa, pb):
            ca, cb = _chars(a), _chars(b)
            sm = SequenceMatcher(None, "".join(c for c, _ in ca), "".join(c for c, _ in cb),
                                 autojunk=False)
            for op, i1, i2, j1, _j2 in sm.get_opcodes():
                if op != "equal":
                    continue
                for k in range(i2 - i1):
                    ch, fa = ca[i1 + k]
                    if ch not in FILLERS and fa != cb[j1 + k][1]:
                        changed += 1
    return changed


def compare(original: Path, filled: Path) -> bool:
    parts_b, parts_a = _parts(original), _parts(filled)
    before, after = measure(parts_b), measure(parts_a)
    ok = True
    print(f"== {original.name} → {filled.name}")
    for name in before:
        b, a = before[name], after[name]
        if name == "方框符號":
            bad = False                     # 只列出來參考
        else:
            bad = a > b if name in MORE_IS_BAD else a < b
        ok &= not bad
        if bad or b or a:
            print(f"  {'壞了' if bad else '　　'} {name:24} {b:>4} → {a}")
    changed = format_changes(parts_b, parts_a)
    bad = changed != 0
    ok &= not bad
    print(f"  {'壞了' if bad else '　　'} {'印好的字格式被改（字數）':22} "
          + ("段落數對不上" if changed < 0 else f"{changed}"))
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

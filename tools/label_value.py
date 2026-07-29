"""把履歷整理成最終的「標籤值清單」JSON，兩種路線：

  from-lines  ：拿逐行辨識結果（vision_lines.py 的輸出）當輸入，純文字整理
  from-images ：截圖直接給視覺模型，一步到位
  both        ：兩種都跑（預設），輸出兩個檔案方便對照

用法：
    python tools/label_value.py [both|from-lines|from-images]

輸出：
    output/data/{名稱}_refined.json   ← from-lines
    output/data/{名稱}_direct.json    ← from-images

輸出形狀：
  from-lines  → {"items": [{"section": "工作經歷", "entry": 1,
                            "label": "公司名稱", "value": "範例科技"}, ...]}
               section＝區塊標題、entry＝同區塊第幾筆，只收有填寫的內容。
  from-images → [{"label": "中文姓名", "value": "王小明"}, ...]
               正式 prompt 定案版：純標籤值陣列，未填的欄位 value 給空字串。
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.core import llm  # noqa: E402

DATA_DIR = ROOT / "output" / "data"
IMG_DIR = ROOT / "input" / "imagesz"

RULES = """輸出規則：
1. 只收「求職者填寫的內容」。空白欄位、表頭、欄名、說明文字、
   法律條款、頁尾一律不收。
2. label 用表格上印的欄位名（如 中文姓名、公司名稱、瞭解程度）。
3. 勾選題的 value 是被勾中的那一項（如 性別 → 男）。
4. 同一區塊有多筆資料（多段學歷、多家公司）用 entry 1、2、3 區分，
   單筆資料 entry 一律是 1。
5. 一格裡混了多個欄位（公司：X 部門：Y）要拆成多個配對。"""

SYSTEM_LINES = "你是履歷資料整理器。輸入是對一份履歷表逐行辨識的結果，" \
               "請整理成最終的標籤值清單。\n" + RULES + """
6. section 的決定方式，依序：
   (1) 行首有〔〕標註（來自文件的區塊標題）→ 照抄那個區塊名，
       不要自己發明或改寫；混了多個名字時（表格左右並排），
       挑內容實際所屬的那一個。
   (2) 整份輸入都沒有〔〕標註（這份文件沒有區塊標題）→ 依內容給
       一個合理的分類（如 基本資料、學歷、工作經歷）。
   (3) 連內容都判斷不出來 → section 給空字串，不要硬掰。"""

# 截圖直出的正式 prompt（在 tests/prompt_test.py 驗證後定案）。
# 照實驗時的形式放在 user 訊息、不掛 system prompt，重現同樣的行為。
SYSTEM_IMAGES = """請讀出這張履歷表截圖的內容，輸出 JSON 陣列，
每個元素是 {"label": 欄位名, "value": 填寫的內容}，若該欄位未填則輸出空字串。"""

# 直出路線的輸出形狀：頂層就是陣列，跟 prompt 說的一致。
# maxItems 是防跳針的保險，不是預期上限。
SCHEMA_DIRECT = {
    "type": "array",
    "maxItems": 200,
    "items": {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "value": {"type": "string"},
        },
        "required": ["label", "value"],
    },
}

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 80,
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "entry": {"type": "integer"},
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["section", "entry", "label", "value"],
            },
        }
    },
    "required": ["items"],
}


def latest_lines_file() -> Path:
    files = [p for p in DATA_DIR.glob("*.json")
             if not p.stem.endswith(("_refined", "_direct"))]
    if not files:
        sys.exit(f"{DATA_DIR} 裡沒有逐行辨識結果，先跑 python tools/vision_lines.py")
    return max(files, key=lambda p: p.stat().st_mtime)


def _clean_heading(text: str) -> str:
    """標題行的裝飾符號因表格而異（【】、◆、底線…），只做通用的頭尾修剪。"""
    return re.sub(r"^[\W_]+|[\W_]+$", "", text, flags=re.UNICODE) or text


def from_lines(src: Path) -> dict:
    doc = json.loads(src.read_text(encoding="utf-8"))
    all_lines = [l for p in doc["pages"] for l in p["lines"]]
    # 區塊歸屬不猜也不寫死符號：辨識階段由模型看排版標出 heading 行
    # （通用於任何表格），這裡只做確定性的「往下傳遞」，整理模型照抄。
    # 整份文件連一個標題都沒有（自由排版履歷）就不加〔〕，
    # 讓整理模型依內容推斷——硬掛「表頭」只會逼它照抄一個錯的答案。
    has_headings = any(l.get("heading") for l in all_lines)
    # 餵緊湊版就好：只留每行的原文與初步配對，省下的 token 留給輸出
    compact = []
    section = "表頭"
    for line in all_lines:
        if line.get("heading"):
            section = _clean_heading(line["text"])
        pairs = "；".join(f"{f['label']}={f['value']}" for f in line["fields"])
        prefix = f"〔{section}〕" if has_headings else ""
        compact.append(f"{prefix}{line['text']}" + (f"　→ {pairs}" if pairs else ""))
    user = "逐行辨識結果：\n" + "\n".join(compact)
    return llm.ask(config.LLM_HOST, SYSTEM_LINES, user, SCHEMA,
                   model=config.LLM_MODEL, label="標籤值整理(逐行)")


def from_images(stem: str) -> dict:
    pngs = sorted(IMG_DIR.glob(f"{stem}_p*.png")) or sorted(IMG_DIR.glob("*.png"))
    if not pngs:
        sys.exit(f"{IMG_DIR} 裡沒有截圖，先跑 python tools/snapshot.py")
    if not llm.supports_vision(config.LLM_HOST):
        sys.exit("目前的推論引擎看不了圖片：請先補下載視覺檔並重新啟動模型")
    content = [{"type": "text", "text": SYSTEM_IMAGES}]
    for png in pngs:
        b64 = base64.b64encode(png.read_bytes()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return llm.ask(config.LLM_HOST, "", content, SCHEMA_DIRECT,
                   model=config.LLM_MODEL, label=f"標籤值直出(視覺{len(pngs)}頁)")


def run(name: str, worker, dest: Path) -> None:
    t0 = time.perf_counter()
    try:
        result = worker()
    except llm.LlmUnavailable as e:
        print(f"{name}：失敗（{e}）")
        return
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # 直出是頂層陣列，逐行整理是 {items: [...]}
    count = len(result) if isinstance(result, list) else len(result.get("items", []))
    print(f"{name}：{count} 項，{int(time.perf_counter() - t0)} 秒 → {dest}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    src = latest_lines_file()
    stem = src.stem
    if mode in ("both", "from-lines"):
        run("逐行整理", lambda: from_lines(src), DATA_DIR / f"{stem}_refined.json")
    if mode in ("both", "from-images"):
        run("截圖直出", lambda: from_images(stem), DATA_DIR / f"{stem}_direct.json")


if __name__ == "__main__":
    main()

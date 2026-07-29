"""讓視覺模型把履歷截圖「一行一行」讀出來，輸出結構化 JSON。

用法：
    python tools/vision_lines.py [圖片目錄] [輸出目錄]

預設讀 input/imagesz/、寫到 output/data/{檔名}.json。
每頁單獨呼叫一次模型：任務小、注意力集中，比一次塞整份可靠。

輸出形狀：
{
  "source": "...", "model": "...", "generated_at": "...",
  "pages": [
    {"page": 1,
     "lines": [
       {"text": "中文姓名： 王小明  英文名： Ming", "heading": false,
        "fields": [{"label": "中文姓名", "value": "王小明"},
                   {"label": "英文名", "value": "Ming"}]},
       ...]}
  ]
}
text 是那一行的照抄（表格列用「 | 」分隔），heading 是「這行是不是
區塊標題」——由模型看排版判斷，不寫死任何符號，換別家表格也通用；
fields 是「標籤：值」的配對——沒填的值給空字串，勾選題給被勾中的那一項。
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402
from backend.core import llm  # noqa: E402

SYSTEM_PROMPT = """你是表單逐行閱讀器。使用者給你一張履歷表的頁面截圖，
請由上到下、由左到右，一行一行讀出內容。

規則：
1. text：這一行的文字照抄。表格的一列算一行，儲存格之間用「 | 」分隔。
2. fields：這一行裡「標籤：值」的配對。
   - 值是空白（底線、空格）→ value 給空字串。
   - 勾選題（■□）→ value 給被勾中（■）的那一項；都沒勾給空字串。
   - 純標題、說明文字沒有配對 → fields 給空陣列。
3. heading：這一行是不是「區塊標題」——把整份表格分成幾大段的那種
   標題行（例如 基本資料、教育程度、工作經歷），通常粗體、置中、
   字較大或帶特殊符號。表格裡的欄名列（姓名｜公司｜職稱）不算標題。
4. 不要跳行、不要把多行合併成一行、不要自己補文字。
5. 每一行只輸出一次。頁面接近空白就只列實際有字的那幾行，
   完全沒有內容就回傳空的 lines。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "maxItems": 60,   # 文法層的保險：空白頁會讓模型重複跳針直到撞上下文
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "heading": {"type": "boolean"},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["label", "value"],
                        },
                    },
                },
                "required": ["text", "heading", "fields"],
            },
        }
    },
    "required": ["lines"],
}


def read_page(png: bytes, page_no: int) -> list:
    b64 = base64.b64encode(png).decode("ascii")
    content = [
        {"type": "text", "text": "請逐行讀出這一頁。"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]
    result = llm.ask(config.LLM_HOST, SYSTEM_PROMPT, content, SCHEMA,
                     model=config.LLM_MODEL, label=f"逐行辨識 p{page_no}")
    return result.get("lines", [])


def main() -> None:
    img_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "input" / "imagesz"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "output" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    pngs = sorted(img_dir.glob("*.png"))
    if not pngs:
        sys.exit(f"{img_dir} 裡沒有截圖，先跑 python tools/snapshot.py")
    if not llm.supports_vision(config.LLM_HOST):
        sys.exit("目前的推論引擎看不了圖片：請先補下載視覺檔並重新啟動模型")

    # 檔名長相是 {docx名}_p{頁碼}.png，取共同前綴當輸出檔名
    stem = re.sub(r"_p\d+$", "", pngs[0].stem)

    pages = []
    for i, png in enumerate(pngs, 1):
        t0 = time.perf_counter()
        try:
            lines = read_page(png.read_bytes(), i)
        except llm.LlmUnavailable as e:
            # 單頁失敗（空白頁跳針之類）記下來就好，別讓整份陪葬
            print(f"第 {i}/{len(pngs)} 頁：失敗（{e}）")
            pages.append({"page": i, "image": png.name, "lines": [], "error": str(e)})
            continue
        print(f"第 {i}/{len(pngs)} 頁：{len(lines)} 行，"
              f"{int(time.perf_counter() - t0)} 秒（{png.name}）")
        pages.append({"page": i, "image": png.name, "lines": lines})

    doc = {
        "source": stem,
        "model": config.LLM_MODEL,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pages": pages,
    }
    dest = out_dir / f"{stem}.json"
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    total_lines = sum(len(p["lines"]) for p in pages)
    total_fields = sum(len(l["fields"]) for p in pages for l in p["lines"])
    print(f"完成 → {dest}")
    print(f"共 {len(pages)} 頁、{total_lines} 行、{total_fields} 個標籤值配對")


if __name__ == "__main__":
    main()

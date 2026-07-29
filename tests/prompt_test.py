"""最小 prompt 實驗：一張截圖＋一段 prompt 丟給模型，看它輸出什麼。

把 PROMPT 改成你要測的內容，然後：

    python tests/prompt_test.py                # 用 input/imagesz 裡最新的截圖
    python tests/prompt_test.py 路徑/xxx.png   # 指定某張截圖

模型輸出原樣印出，並存到 output/data/test/。
"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402

# ═══════════════════ PROMPT 填這裡 ═══════════════════
PROMPT = """請讀出這張履歷表截圖的內容，輸出 JSON 陣列，
每個元素是 {"label": 欄位名, "value": 填寫的內容}，若該欄位未填則輸出空字串。"""
# ═════════════════════════════════════════════════════

OUT_DIR = ROOT / "output" / "data" / "test"


def main() -> None:
    if len(sys.argv) > 1:
        image = Path(sys.argv[1])
    else:
        # 檔名排序取第一張（xxx_p1.png）。不能用 mtime：多頁是同一批寫入的，
        # 「最新」會落在最後一頁，時間戳還常平手
        pngs = sorted((ROOT / "input" / "imagesz").glob("*.png"))
        if not pngs:
            sys.exit("input/imagesz 裡沒有截圖，先跑 python tools/snapshot.py")
        image = pngs[0]

    b64 = base64.b64encode(image.read_bytes()).decode("ascii")
    payload = {
        "model": config.LLM_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    print(f"截圖：{image.name}　模型：{config.LLM_MODEL}")
    t0 = time.perf_counter()
    r = requests.post(f"{config.LLM_HOST}/v1/chat/completions",
                      json=payload, timeout=600)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"] or ""
    print(f"耗時 {int(time.perf_counter() - t0)} 秒\n")
    print(content)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{image.stem}_result.txt"
    dest.write_text(content, encoding="utf-8")
    print(f"\n已存 → {dest}")


if __name__ == "__main__":
    main()

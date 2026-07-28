"""
本地 LLM 後端
------------------------------------------------
只做一件事：把「一批不認得的標籤」丟給小模型，要它從白名單裡選欄位。

關鍵手法是 **受限解碼 (constrained decoding)**：
把 JSON Schema 交給推論引擎，引擎在每一步只允許符合 schema 的 token 被取樣，
所以「輸出不是合法 JSON」或「模型自己發明欄位名稱」在機制上就不可能發生。

推論引擎固定使用 llama.cpp：對 llama-server 的 OpenAI 相容端點傳
response_format.json_schema，llama.cpp 會把它編成 GBNF grammar 來約束取樣。

因為輸出被鎖死成一個 enum 選擇題，這件事 4B 的模型就做得很好，
不需要 70B 等級的模型（預設 Qwen3.5-4B-Instruct-Q4_K_M）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

import requests

from .schema import FIELD_KEYS, describe_fields_for_llm

log = logging.getLogger(__name__)

HEALTH_TIMEOUT = 0.5

SYSTEM_PROMPT = """你是履歷表單的欄位對映器。使用者會給你一份 Word 履歷表中「待填空格」的清單。
你的工作：為每一個空格，從給定的欄位代碼清單中挑出唯一正確的一個。

規則：
1. 只能使用清單裡出現過的欄位代碼，不可自創。
2. 看不出來要填什麼、或那格根本不是給求職者填的（例如「面試評語」「人事室填寫」），選 __SKIP__。
3. 確定是求職者要填、但清單沒有對應項目，選 __UNKNOWN__。
4. confidence 是 0.0~1.0 的信心值，不確定就給低分，不要硬猜高分。
5. 每個 anchor_id 只輸出一次，且必須輸出全部的 anchor_id。"""


def _build_schema(anchor_ids: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "minItems": len(anchor_ids),
                "maxItems": len(anchor_ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "anchor_id": {"type": "string", "enum": anchor_ids},
                        "field_key": {"type": "string", "enum": FIELD_KEYS},
                        "confidence": {"type": "number"},
                    },
                    "required": ["anchor_id", "field_key", "confidence"],
                },
            }
        },
        "required": ["mappings"],
    }


def _build_user_prompt(anchors: List[Dict[str, Any]]) -> str:
    lines = ["可用的欄位代碼：", describe_fields_for_llm(), "", "待判斷的空格："]
    for a in anchors:
        extra = f"（選項：{'、'.join(a['options'])}）" if a.get("options") else ""
        lines.append(
            f"- anchor_id={a['id']} | 型態={a['kind']} | 標籤文字=「{a['label']}」{extra}\n"
            f"    同列上下文：{a.get('context', '')[:160]}"
        )
    return "\n".join(lines)


class BaseBackend:
    name = "base"

    def map_anchors(self, anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def available(self) -> bool:
        return False


class LlamaCppBackend(BaseBackend):
    """llama-server --jinja -m model.gguf ；OpenAI 相容端點。"""
    name = "llamacpp"

    def __init__(self, model: str = "local",
                 host: str = "http://localhost:8085", timeout: int = 180):
        self.model, self.host, self.timeout = model, host, timeout

    def available(self) -> bool:
        # 探測 localhost 的服務：活著就是毫秒級回應，逾時放長只會讓前端的
        # 狀態燈每次輪詢都卡好幾秒（模型沒開時尤其明顯）
        try:
            return requests.get(f"{self.host}/health",
                                timeout=HEALTH_TIMEOUT).status_code == 200
        except Exception:
            return False

    def map_anchors(self, anchors):
        if not anchors:
            return []
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(anchors)},
            ],
            "temperature": 0,
            # Qwen3.5 預設開啟 thinking，會先吐數千字推理才給答案，
            # 常常在輸出 JSON 之前就撞到長度上限（finish_reason=length）。
            # 本任務是分類題，不需要推理，一律關掉。
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "field_mapping",
                    "strict": True,
                    "schema": _build_schema([a["id"] for a in anchors]),
                },
            },
        }
        t0 = time.perf_counter()
        r = requests.post(f"{self.host}/v1/chat/completions",
                          json=payload, timeout=self.timeout)
        r.raise_for_status()
        choice = r.json()["choices"][0]
        elapsed = int((time.perf_counter() - t0) * 1000)
        finish = choice.get("finish_reason")
        content = choice["message"]["content"] or ""

        log.info("模型呼叫 batch=%d finish=%s 回應=%d字 耗時=%dms",
                 len(anchors), finish, len(content), elapsed)
        # finish=length 幾乎都代表 thinking 沒關成功：模型把預算燒在推理上，
        # content 是空的。沒有這條 log 就只會看到一個難解的 JSON 解析錯誤。
        if finish == "length" or not content:
            raise RuntimeError(
                f"模型未產生有效輸出（finish_reason={finish}，content 長度 {len(content)}）"
                "，請確認 thinking 已關閉")
        return json.loads(content).get("mappings", [])


class NullBackend(BaseBackend):
    """沒有安裝模型時的降級模式：不做任何 AI 判斷，全部交給人工確認。"""
    name = "null"

    def available(self) -> bool:
        return True

    def map_anchors(self, anchors):
        return [{"anchor_id": a["id"], "field_key": "__UNKNOWN__", "confidence": 0.0}
                for a in anchors]


def get_backend(cfg: Dict[str, Any]) -> BaseBackend:
    kind = (cfg.get("backend") or "llamacpp").lower()
    if kind == "null":
        return NullBackend()
    host = cfg.get("host", "http://localhost:8085")
    b = LlamaCppBackend(cfg.get("model", "local"), host)
    if b.available():
        return b
    # 模型沒開不該擋住整個流程：規則層與快取仍能解掉大部分欄位。
    log.warning("llama-server 無法連線 host=%s，降級為人工模式", host)
    return NullBackend()

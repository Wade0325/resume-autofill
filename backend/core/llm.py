"""llama-server 客戶端。

輸出用 JSON Schema 約束：llama.cpp 會把 schema 編成 GBNF，
每一步只允許符合文法的 token 被取樣，所以模型不可能吐出不合法的 JSON
或不存在的欄位代碼。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

import requests

log = logging.getLogger(__name__)

HEALTH_TIMEOUT = 0.5   # localhost 服務活著就是毫秒級回應
CALL_TIMEOUT = 600


class LlmUnavailable(RuntimeError):
    pass


def available(host: str) -> bool:
    try:
        return requests.get(f"{host}/health", timeout=HEALTH_TIMEOUT).status_code == 200
    except Exception:
        return False


def ask(host: str, system: str, user: str, schema: Dict[str, Any],
        model: str = "local", label: str = "") -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        # Qwen3.5 預設開 thinking，會把輸出預算燒在推理上，
        # 常常還沒吐出 JSON 就撞到長度上限
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        },
    }

    t0 = time.perf_counter()
    try:
        r = requests.post(f"{host}/v1/chat/completions", json=payload, timeout=CALL_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        raise LlmUnavailable(f"模型服務無法連線（{host}）：{e}") from e

    choice = r.json()["choices"][0]
    content = choice["message"]["content"] or ""
    log.info("模型呼叫 %s 提示=%d字 finish=%s 回應=%d字 耗時=%dms",
             label or "-", len(user), choice.get("finish_reason"), len(content),
             int((time.perf_counter() - t0) * 1000))

    if choice.get("finish_reason") == "length" or not content:
        raise LlmUnavailable(
            f"模型未產生有效輸出（finish_reason={choice.get('finish_reason')}）")
    return json.loads(content)

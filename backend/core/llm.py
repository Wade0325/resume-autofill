"""llama-server 客戶端。

輸出用 JSON Schema 約束：llama.cpp 會把 schema 編成 GBNF，
每一步只允許符合文法的 token 被取樣，所以模型不可能吐出不合法的 JSON
或不存在的欄位代碼。
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import nullcontext
from typing import Any, Dict, List, Union

import requests

log = logging.getLogger(__name__)

HEALTH_TIMEOUT = 0.5   # localhost 服務活著就是毫秒級回應
CALL_TIMEOUT = 600


_LANGFUSE: Any = False    # False＝還沒初始化，None＝確定不啟用


def _tracer():
    """開發時把每次呼叫送進 Langfuse。沒設金鑰或沒裝套件就完全不啟用——
    正式版不該多一個相依，也不該把履歷內容送去任何地方。"""
    global _LANGFUSE
    if _LANGFUSE is not False:
        return _LANGFUSE
    _LANGFUSE = None
    base = os.environ.get("LANGFUSE_BASE_URL", "")
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    # SDK 沒指定位址時的預設值是 cloud.langfuse.com。提示詞裡是完整的履歷，
    # 少設一個變數就把個資送上雲端——寧可不啟用也不能走那條路
    if "cloud.langfuse.com" in base or not base:
        log.warning("Langfuse 未啟用：LANGFUSE_BASE_URL 沒指向自架位址")
        return None
    try:
        from langfuse import Langfuse
        _LANGFUSE = Langfuse(base_url=base)
    except Exception as e:
        log.warning("Langfuse 未啟用：%s", e)
    return _LANGFUSE


class LlmUnavailable(RuntimeError):
    pass


def available(host: str) -> bool:
    try:
        return requests.get(f"{host}/health", timeout=HEALTH_TIMEOUT).status_code == 200
    except Exception:
        return False


def supports_vision(host: str) -> bool:
    """llama-server 有掛 mmproj 時，/props 會回報 vision 能力。

    偵測不到就當沒有——手動啟動的舊版 server 寧可走純文字，
    也不要把圖片丟給一個看不懂的服務。
    """
    try:
        props = requests.get(f"{host}/props", timeout=HEALTH_TIMEOUT).json()
        return bool(props.get("modalities", {}).get("vision"))
    except Exception:
        return False


# user 可以是純文字，或 OpenAI 格式的多段內容（文字＋data URI 圖片）
UserContent = Union[str, List[Dict[str, Any]]]


def _traceable(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """截圖是幾百 KB 的 base64，送進 trace 只會把畫面塞爆，換成標記。"""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = [p if p.get("type") != "image_url" else {"type": "image_url", "image_url": "<截圖>"}
                       for p in content]
        out.append({**m, "content": content})
    return out


def ask(host: str, system: str, user: UserContent, schema: Dict[str, Any],
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

    prompt_chars = len(user) if isinstance(user, str) else sum(
        len(p.get("text", "")) for p in user if isinstance(p, dict))
    images = 0 if isinstance(user, str) else sum(
        1 for p in user if isinstance(p, dict) and p.get("type") == "image_url")

    tracer = _tracer()
    traced = (tracer.start_as_current_generation(
        name=label or "llm", model=model,
        input=_traceable(payload["messages"]),
        metadata={"schema": schema, "images": images})
        if tracer else nullcontext())

    t0 = time.perf_counter()
    with traced as generation:
        try:
            r = requests.post(f"{host}/v1/chat/completions", json=payload, timeout=CALL_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            raise LlmUnavailable(f"模型服務無法連線（{host}）：{e}") from e

        body = r.json()
        choice = body["choices"][0]
        content = choice["message"]["content"] or ""
        if generation is not None:
            usage = body.get("usage") or {}
            generation.update(output=content, usage_details={
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0)})

    log.info("模型呼叫 %s 提示=%d字 圖片=%d finish=%s 回應=%d字 耗時=%dms",
             label or "-", prompt_chars, images, choice.get("finish_reason"), len(content),
             int((time.perf_counter() - t0) * 1000))

    if choice.get("finish_reason") == "length":
        raise LlmUnavailable(
            "這份文件超出模型的上下文長度，輸出被截斷。"
            "請用更大的 --ctx-size 重啟 llama-server（目前的提示詞約 "
            f"{prompt_chars // 2} tokens）")
    if not content:
        raise LlmUnavailable("模型沒有回傳任何內容")
    return json.loads(content)

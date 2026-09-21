"""llama-server 客戶端。

輸出用 JSON Schema 約束：llama.cpp 會把 schema 編成 GBNF，
每一步只允許符合文法的 token 被取樣，所以模型不可能吐出不合法的 JSON
或不存在的欄位代碼。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import requests

log = logging.getLogger(__name__)

HEALTH_TIMEOUT = 0.5   # localhost 服務活著就是毫秒級回應
CALL_TIMEOUT = 600

# llama-server 預設對所有網站開放 CORS、又不驗身分：任何網頁都能叫它讀 /slots、借 GPU
# 跑推論。後端啟動它時用環境變數 LLAMA_API_KEY 給金鑰（model_manager），這裡的呼叫帶上
# 同一把。/health 是公開的不必帶；手動啟動、沒設金鑰的 server 收到金鑰標頭也照常回應
_KEY: Optional[str] = None


def key_file() -> Path:
    """金鑰檔的位置，跟 config.HOME 同一套規則（core 不引用 config）——
    研究迴圈直接呼叫 filler，也要找得到產品後端啟動的那個 server 的金鑰。"""
    root = Path(os.environ.get("RESUME_AUTOFILL_ROOT", Path(__file__).resolve().parents[2]))
    return Path(os.environ.get("RESUME_AUTOFILL_HOME", root / "data")) / "llm.key"


def ensure_key() -> str:
    """啟動 llama-server 前呼叫：金鑰檔不在就產生一把，回傳金鑰。
    順便更新快取——檔案被換過的話，之後的呼叫才跟新啟動的 server 對得上。"""
    global _KEY
    path = key_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32) + "\n", encoding="ascii")
    _KEY = path.read_text(encoding="ascii").strip()
    return _KEY


def _auth() -> Dict[str, str]:
    global _KEY
    if _KEY is None and key_file().exists():
        _KEY = key_file().read_text(encoding="ascii").strip()
    return {"Authorization": f"Bearer {_KEY}"} if _KEY else {}


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


class LlmError(RuntimeError):
    """模型層的錯誤基底。看版面逐批判讀的地方抓這個：一批失敗不拖垮整份，其餘照跑。
    其他步驟不抓——模型出問題時分析直接失敗，由 service 告訴使用者原因。"""


class LlmUnavailable(LlmError):
    """連不上 llama-server：模型沒啟動或掛了。"""


class LlmCallFailed(LlmError):
    """模型活著但這次呼叫失敗（上下文不夠被截斷、空回應）。
    訊息要能直接給使用者看——這不是「去啟動模型」能解決的。"""


class LlmContextFull(LlmCallFailed):
    """提示加回答超過了伺服器的上下文：提示送不進去（HTTP 400），或回答寫到一半撞滿
    （finish_reason=length，llama-server 的 n_predict 預設不設限，撞到的只會是上下文）。
    跟其他失敗分開，是因為接著問的那串對話要整串重開才問得成，見 `Chat.ask`。"""


def available(host: str) -> bool:
    try:
        return requests.get(f"{host}/health", timeout=HEALTH_TIMEOUT).status_code == 200
    except Exception:
        return False


def context_size(host: str) -> int:
    """這台 llama-server 的單一請求實際能用多長的上下文。問不到就回 0。

    要問伺服器，不要讀 `config.LLM_CTX_SIZE`：那是我們「要求」的值，llama.cpp 會依
    模型訓練長度往下夾，`--parallel N` 時每個請求也只拿到其中一份。`/props` 的
    `default_generation_settings.n_ctx` 就是 llama-server 自己 log 的 `n_ctx_slot`，
    單一請求的真值。使用者也可能自己啟動 server，那時環境變數更是完全不算數。
    """
    try:
        props = requests.get(f"{host}/props", headers=_auth(), timeout=HEALTH_TIMEOUT).json()
        return int(props.get("default_generation_settings", {}).get("n_ctx") or 0)
    except Exception:
        return 0


def supports_vision(host: str) -> bool:
    """llama-server 有掛 mmproj 時，/props 會回報 vision 能力。

    偵測不到就當沒有——手動啟動的舊版 server 寧可走純文字，
    也不要把圖片丟給一個看不懂的服務。
    """
    try:
        props = requests.get(f"{host}/props", headers=_auth(), timeout=HEALTH_TIMEOUT).json()
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
            content = [p if p.get("type") != "image_url"
                       else {"type": "image_url", "image_url": "<截圖>"}
                       for p in content]
        out.append({**m, "content": content})
    return out


def ask(host: str, system: str, user: UserContent, schema: Dict[str, Any],
        model: str = "local", label: str = "") -> Dict[str, Any]:
    """問一次就結束。連著問好幾批的用 Chat，那樣快取才吃得到。"""
    return _call(host, [{"role": "system", "content": system},
                        {"role": "user", "content": user}],
                 schema, model, label)[0]


def _call(host: str, messages: List[Dict[str, Any]], schema: Dict[str, Any],
          model: str, label: str) -> Tuple[Dict[str, Any], str, int]:
    payload = {
        "model": model,
        # 複製一份：Chat 傳進來的就是它自己那串對話，回來之後馬上會接上 assistant 訊息。
        # 直接放進 payload 的話，任何留著 payload 的人（vlm_proof 就是）手上那份會跟著變
        "messages": list(messages),
        "temperature": 0,
        # Qwen3.5 預設開 thinking，會把輸出預算燒在推理上，
        # 常常還沒吐出 JSON 就撞到長度上限
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        },
    }

    last = messages[-1]["content"]
    prompt_chars = len(last) if isinstance(last, str) else sum(
        len(p.get("text", "")) for p in last if isinstance(p, dict))
    images = 0 if isinstance(last, str) else sum(
        1 for p in last if isinstance(p, dict) and p.get("type") == "image_url")
    turns = sum(1 for m in messages if m["role"] == "user")

    tracer = _tracer()
    traced = (tracer.start_as_current_generation(
        name=label or "llm", model=model,
        input=_traceable(payload["messages"]),
        metadata={"schema": schema, "images": images})
        if tracer else nullcontext())

    t0 = time.perf_counter()
    with traced as generation:
        # 只有「連不上」才是模型沒啟動（LlmUnavailable）；逾時、文件太長、服務出錯、
        # 回應看不懂都是模型活著但這次失敗（LlmCallFailed）——叫使用者去啟動模型只會鬼打牆，
        # 而看版面分批呼叫時，這一類只略過那一批、不會讓整份分析失敗
        try:
            r = requests.post(f"{host}/v1/chat/completions", json=payload, headers=_auth(),
                              timeout=CALL_TIMEOUT)
        except requests.ConnectionError as e:          # 含連線逾時（ConnectTimeout 兩邊都算）
            raise LlmUnavailable(f"模型服務無法連線（{host}）：{e}") from e
        except requests.Timeout as e:
            raise LlmCallFailed(
                f"模型 {CALL_TIMEOUT // 60} 分鐘內沒有回應。沒有獨立顯示卡時推論很慢，"
                "請確認右上角的模型已就緒，或換用顯示卡跑得動的模型") from e
        except requests.RequestException as e:
            raise LlmUnavailable(f"模型服務無法連線（{host}）：{e}") from e
        if r.status_code == 401:
            raise LlmUnavailable("模型服務的金鑰對不上，請從右上角的模型選單重新啟動模型")
        if r.status_code >= 400:
            raise _http_problem(r)
        try:
            body = r.json()
            choice = body["choices"][0]
            content = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LlmCallFailed(f"模型服務回了看不懂的內容：{e}") from e
        usage = body.get("usage") or {}
        if generation is not None:
            generation.update(output=content, usage_details={
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0)})

    log.info("模型呼叫 %s 提示=%d字 圖片=%d 第%d輪 finish=%s 回應=%d字 耗時=%dms",
             label or "-", prompt_chars, images, turns, choice.get("finish_reason"),
             len(content), int((time.perf_counter() - t0) * 1000))

    if choice.get("finish_reason") == "length":
        raise LlmContextFull(
            "這份文件太長，超出模型一次能讀的長度，輸出被截斷（提示詞約 "
            f"{prompt_chars // 2} tokens）。開發者可用 RESUME_AUTOFILL_LLM_CTX "
            "加大上下文後重新啟動模型")
    if not content:
        raise LlmCallFailed("模型沒有回傳任何內容")
    # 原文與用量一併回傳：接著問的那幾批要把原文當成 assistant 訊息接回對話裡，
    # 用量是這串對話現在的實際長度。prompt_tokens 算的是整段提示，快取命中的前綴也算在內
    # （實測接著問只重算 27 個 token 的那一次，回報的是整段 801）；舊版沒回報就是 0
    prompt_tokens = usage.get("prompt_tokens") or 0
    used = prompt_tokens + (usage.get("completion_tokens") or 0) if prompt_tokens else 0
    try:
        return json.loads(content), content, used
    except ValueError as e:
        raise LlmCallFailed(f"模型回傳的內容不完整，不是合法的 JSON：{e}") from e


def _http_problem(r: requests.Response) -> LlmCallFailed:
    """llama-server 回錯誤時，換成帶白話說明的例外。它的錯誤格式是
    {"error": {"code": 400, "type": "exceed_context_size_error", "message": ...}}。"""
    try:
        err = r.json().get("error") or {}
    except ValueError:
        err = {}
    detail = err.get("message") or r.text[:200]
    if err.get("type") == "exceed_context_size_error":
        return LlmContextFull(
            "這份文件太長，超出模型一次能讀的長度"
            f"（需要 {err.get('n_prompt_tokens', '?')} tokens，上限 {err.get('n_ctx', '?')}）")
    if r.status_code >= 500:
        return LlmCallFailed(f"模型服務出錯（HTTP {r.status_code}）：{detail}")
    return LlmCallFailed(f"模型拒絕了這次請求（HTTP {r.status_code}）：{detail}")


DEFAULT_CTX = 16384   # 問不到伺服器的上下文時的假設：產品啟動 llama-server 的預設值
                      # （config.LLM_CTX_SIZE；core 不引用 config）
PAGE_TOKENS = 1600    # 一整張示意圖（1100×1500）送進模型的 token，llama-server 實測；
                      # 不滿一頁的比較少（三份考題的最後一頁 716～988）
ANSWER_ROOM = 500     # 留給回答的空間：配對一批 16 格，回答約 300


def _estimate(content: UserContent) -> int:
    """還沒送出去的訊息估多長，寧可估多：文字一個字算一個 token（我們的提示實測
    平均 0.5，中文多的段落最高 0.86），圖片一律當成整頁。"""
    if isinstance(content, str):
        return len(content)
    return sum(PAGE_TOKENS if p.get("type") == "image_url" else len(p.get("text", ""))
               for p in content)


class Chat:
    """同一輪裡連著問的好幾批，接在同一串對話後面問。

    llama-server 的提示快取只有在「新的提示完整包含上一次的提示」時才重用；一旦中途
    分岔，就退回第一張圖之前整份重算。實測同一段前綴（個人資料＋兩張示意圖）：
    每一批各開一份新提示要重算 4013 個 token（4.6 秒），接在後面問只重算新接上的
    那一段（32 個 token、0.39 秒）。

    所以個人資料與示意圖只在第一次附上，後面幾批只接新的問題——既是省事，也是
    「不分岔」這件事本身的要求。
    """

    def __init__(self, host: str, system: str, model: str = "local") -> None:
        self.host, self.model = host, model
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        self._sent: set = set()
        self._counted = (0, 0)       # (伺服器報的長度, 那時對話有幾則)
        self._ctx: Optional[int] = None

    def seen(self, key: str) -> bool:
        """這串對話裡已經附過這個東西了嗎（個人資料、某一張示意圖）。

        純查詢，不記帳。附了什麼要等 `ask()` 真的問成才算數，由呼叫端用 `attached`
        交代——呼叫端是先組好訊息、最後才呼叫 `ask`，中間那一次要是失敗了，
        記帳早就寫下去的話，下一批會在沒有個人資料、沒有示意圖、連前文都沒有的
        情況下被問，而文法照樣逼模型從欄位清單裡挑一個，猜出來的值會一路寫進輸出。
        """
        return key in self._sent

    def tokens(self) -> int:
        """這串對話現在有多長。

        上一次問成時伺服器報的是實數（整段提示＋回答），之後接上的訊息才用估的。
        以前整串都用估的——文字兩個字算一個 token、圖片一律 1300——中文多的段落少估
        四成、整頁的示意圖少估三百，門檻還沒到，伺服器已經先拒絕了。
        """
        known, upto = self._counted
        return known + sum(_estimate(m["content"]) for m in self.messages[upto:])

    def context(self) -> int:
        """伺服器單一請求實際能用多長（`context_size`）。問不到就當成產品的預設值。"""
        if self._ctx is None:
            self._ctx = context_size(self.host) or DEFAULT_CTX
        return self._ctx

    def start_over(self) -> None:
        """整串重開。`seen` 全部回到 False，個人資料與示意圖要再附一次。"""
        del self.messages[1:]
        self._sent.clear()
        self._counted = (0, 0)

    def start_over_if_long(self, user: UserContent) -> bool:
        """接上這一批就會超過上下文的話，先重開一串，回傳 True——呼叫端要重組這一批，
        個人資料與示意圖要重新附上。

        接著問省的是提示快取，但代價是提示會一直長。撞到上限的後果（這一批問不成）
        比多花一次冷啟動嚴重得多，所以寧可重來。上限要問伺服器，不能寫死：以前寫死
        11000，使用者把 RESUME_AUTOFILL_LLM_CTX 調得比它小，這個閥就永遠不會開，
        對話直接長過上限。已經是新的一串就不重開——重開也不會比較短。
        """
        if len(self.messages) == 1:
            return False
        if self.tokens() + _estimate(user) + ANSWER_ROOM <= self.context():
            return False
        log.info("對話已累積約 %d tokens，接上這一批會超過上下文 %d，重開一串",
                 self.tokens(), self.context())
        self.start_over()
        return True

    def ask(self, user: UserContent, schema: Dict[str, Any], label: str = "",
            attached: Iterable[str] = ()) -> Dict[str, Any]:
        """問這一批。`attached`＝這一次附上了什麼的代號，問成了才記下來。"""
        self.messages.append({"role": "user", "content": user})
        try:
            data, raw, used = _call(self.host, self.messages, schema, self.model, label)
        except LlmContextFull:
            # 這串對話已經塞不下了。只收回這一批的話，下一批接在一樣長的對話上會再撞一次；
            # 沒問成、長度也不會再長，start_over_if_long 永遠不會動作——之後每一批都失敗。
            # 所以整串重開
            self.start_over()
            raise
        except Exception:
            # 其他原因沒問成（逾時、服務出錯……）：把半截的 user 收回，不然下一批會接在
            # 一個沒有回答的問題後面。`attached` 還沒記進 _sent，所以下一批會重新附上
            # 示意圖。對話本身留著，下一批照樣吃得到提示快取
            self.messages.pop()
            raise
        self.messages.append({"role": "assistant", "content": raw})
        self._sent.update(attached)
        if used:
            self._counted = (used, len(self.messages))
        return data

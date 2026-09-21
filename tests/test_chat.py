"""接著問的那一串對話（`llm.Chat`）。

配對那一輪的幾批共用一串對話，個人資料與示意圖只在第一次用到時附上。省下來的是
llama-server 的提示快取，但也因此多了兩種以前不存在的失敗方式：

1. **某一批沒問成的時候，「附過什麼」的帳不能算數**。算數的話，下一批送出去的只有
   位置清單——沒有個人資料、沒有示意圖、連前文都沒有——而 JSON Schema 編成的文法照樣
   逼模型從欄位清單裡挑一個，猜出來的值會一路寫進使用者的 .docx，不會有任何錯誤訊息。
2. **對話會一直長，長過伺服器的上下文**（issue #4）。重開的門檻以前寫死 11000，使用者
   把上下文調得比它小，這個閥就永遠不會開；撞到之後對話又原樣退回，之後每一批都再撞
   一次。三份考題接成的 4 頁長表格在上下文 8200 下，配對 25 批裡連續失敗 20 批。

這一組不需要模型：`llm._call`（或更底層的 HTTP）換成假的。
"""
from __future__ import annotations

import logging
from contextlib import nullcontext

import pytest

from backend.core import filler, llm

HOST = "http://127.0.0.1:8099"


def _fake_call(answer="{}", fail=False, used=0):
    """代替 llm._call。fail=True 就模擬 llama-server 這次掛了，也可以直接給一個例外。
    used 是伺服器回報的用量（整段提示＋回答），0＝舊版 server 沒回報。"""
    seen = []

    def call(host, messages, schema, model, label):
        seen.append([dict(m) for m in messages])
        if fail:
            raise fail if isinstance(fail, Exception) else llm.LlmCallFailed("模擬呼叫失敗")
        return {}, answer, used

    call.seen = seen
    return call


def _compose(chat, text, keys):
    """照 filler.ask 的順序組一批：先問 seen()、最後才 ask()。"""
    attached, user = [], []
    for k in keys:
        if not chat.seen(k):
            attached.append(k)
            user.append({"type": "text", "text": f"<{k}>"})
    user.append({"type": "text", "text": text})
    return user, attached


def test_附過的東西第二批不再附(monkeypatch):
    monkeypatch.setattr(llm, "_call", _fake_call())
    chat = llm.Chat(HOST, "系統提示")

    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)
    user2, att2 = _compose(chat, "batch2", ["fields", "page0"])

    assert att1 == ["fields", "page0"]
    assert att2 == []                                  # 第二批什麼都不必再附
    assert user2 == [{"type": "text", "text": "batch2"}]


def test_沒問成就不算附過(monkeypatch):
    """這一組就是為了擋住那個錯誤：記帳寫在組訊息的時候，失敗時收不回來。"""
    monkeypatch.setattr(llm, "_call", _fake_call(fail=True))
    chat = llm.Chat(HOST, "系統提示")

    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    with pytest.raises(llm.LlmCallFailed):
        chat.ask(user1, {}, attached=att1)

    assert chat.messages == [{"role": "system", "content": "系統提示"}]   # 半截的 user 收回了
    assert not chat.seen("fields")
    assert not chat.seen("page0")

    # 下一批要重新附上，不能只剩位置清單
    user2, att2 = _compose(chat, "batch2", ["fields", "page0"])
    assert att2 == ["fields", "page0"]
    assert len(user2) == 3


def test_問成了才記帳(monkeypatch):
    """失敗一批之後再成功一批：帳只記成功的那一次。"""
    fail = _fake_call(fail=True)
    monkeypatch.setattr(llm, "_call", fail)
    chat = llm.Chat(HOST, "系統提示")
    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    with pytest.raises(llm.LlmCallFailed):
        chat.ask(user1, {}, attached=att1)

    ok = _fake_call()
    monkeypatch.setattr(llm, "_call", ok)
    user2, att2 = _compose(chat, "batch2", ["fields", "page0"])
    chat.ask(user2, {}, attached=att2)

    assert chat.seen("fields") and chat.seen("page0")
    assert [m["role"] for m in chat.messages] == ["system", "user", "assistant"]


def test_送出去的訊息不會跟著之後的改動變(monkeypatch):
    """_call 收到的那份要是對話本體，留著它的人（vlm_proof）手上會拿到之後的樣子。"""
    call = _fake_call()
    monkeypatch.setattr(llm, "_call", call)
    chat = llm.Chat(HOST, "系統提示")

    user1, att1 = _compose(chat, "batch1", ["fields"])
    chat.ask(user1, {}, attached=att1)
    chat.ask([{"type": "text", "text": "batch2"}], {})

    assert len(call.seen[0]) == 2          # 第一次送的是 system＋user
    assert len(call.seen[1]) == 4          # 第二次才是 system＋user＋assistant＋user


# ---------------------------------------------------------------------------
# issue #4 第一項：什麼時候該重開，看伺服器實際的上下文與實際的長度
# ---------------------------------------------------------------------------

_PAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,XX"}}


def test_接上去會超過上下文就重開一串(monkeypatch):
    monkeypatch.setattr(llm, "_call", _fake_call(used=7000))
    monkeypatch.setattr(llm, "context_size", lambda host: 8192)
    chat = llm.Chat(HOST, "系統提示")
    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)

    # 7000＋一整頁 1600＋回答的空間，超過 8192
    assert chat.start_over_if_long([_PAGE, {"type": "text", "text": "batch2"}]) is True
    assert chat.messages == [{"role": "system", "content": "系統提示"}]
    assert not chat.seen("fields")                     # 重開之後要再附一次
    assert not chat.seen("page0")


@pytest.mark.parametrize("n_ctx, start_over", [(9000, True), (16384, False)])
def test_門檻跟著伺服器的上下文(monkeypatch, n_ctx, start_over):
    """以前寫死 11000：上下文 9000 時，8000 的對話還沒到門檻，伺服器已經先拒絕了。"""
    monkeypatch.setattr(llm, "_call", _fake_call(used=8000))
    monkeypatch.setattr(llm, "context_size", lambda host: n_ctx)
    chat = llm.Chat(HOST, "系統提示")
    chat.ask([{"type": "text", "text": "batch1"}], {})

    assert chat.start_over_if_long([{"type": "text", "text": "batch2" * 100}]) is start_over


def test_長度用伺服器報的實數(monkeypatch):
    """字數估起來不到十個 token，伺服器說整段提示加回答是 5000，就是 5000。"""
    monkeypatch.setattr(llm, "_call", _fake_call(used=5000))
    chat = llm.Chat(HOST, "短")
    chat.ask([{"type": "text", "text": "也很短"}], {})
    assert chat.tokens() == 5000


def test_沒回報用量就用估的而且寧可估多(monkeypatch):
    """舊版 server 沒回報用量時才用得到。實測中文多的段落 0.86 token／字、整頁示意圖
    1600——估的要蓋得住，不然門檻還沒到，伺服器已經先拒絕了。"""
    monkeypatch.setattr(llm, "_call", _fake_call(used=0))
    chat = llm.Chat(HOST, "")
    text = "這一批要判斷的位置" * 20
    chat.ask([{"type": "text", "text": text}, _PAGE], {})
    assert chat.tokens() >= 0.86 * len(text) + 1600


def test_圖片按整頁的實測估():
    """一整張示意圖實測 1600。以前抓 1300（兩頁表格整頁加半頁的平均），4 整頁就少估 1200。"""
    chat = llm.Chat(HOST, "")
    chat.messages.append({"role": "user", "content": [_PAGE]})
    assert chat.tokens() == 1600


def test_問不到上下文就當成產品的預設值(monkeypatch):
    monkeypatch.setattr(llm, "context_size", lambda host: 0)
    assert llm.Chat(HOST, "").context() == 16384


def test_新的一串不重開(monkeypatch):
    """重開也不會比較短；要是回報重開了，呼叫端還會白白重組一次。"""
    monkeypatch.setattr(llm, "context_size", lambda host: 100)
    chat = llm.Chat(HOST, "系統提示")
    assert chat.start_over_if_long([{"type": "text", "text": "很長" * 1000}]) is False


# ---------------------------------------------------------------------------
# issue #4 第二項：撞到上下文之後，不能原樣退回讓下一批再撞一次
# ---------------------------------------------------------------------------

def test_撞到上下文就整串重開(monkeypatch):
    """只收回這一批的話，下一批接在一樣長的對話上會再撞一次，長度也不會再長，
    start_over_if_long 永遠不會動作——之後每一批都失敗。"""
    monkeypatch.setattr(llm, "_call", _fake_call(used=3000))
    chat = llm.Chat(HOST, "系統提示")
    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)

    monkeypatch.setattr(llm, "_call", _fake_call(fail=llm.LlmContextFull("太長")))
    with pytest.raises(llm.LlmContextFull):
        chat.ask([{"type": "text", "text": "batch2"}], {})

    assert chat.messages == [{"role": "system", "content": "系統提示"}]
    assert not chat.seen("fields") and not chat.seen("page0")
    assert chat.tokens() < 3000                        # 長度的帳也跟著歸零


def test_其他失敗只收回這一批(monkeypatch):
    """逾時、服務出錯跟對話長短無關：整串重開只會讓下一批多一次冷啟動。"""
    monkeypatch.setattr(llm, "_call", _fake_call())
    chat = llm.Chat(HOST, "系統提示")
    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)
    before = [dict(m) for m in chat.messages]

    monkeypatch.setattr(llm, "_call", _fake_call(fail=True))
    with pytest.raises(llm.LlmCallFailed):
        chat.ask([{"type": "text", "text": "batch2"}], {})

    assert chat.messages == before
    assert chat.seen("fields") and chat.seen("page0")


# 認得出「上下文不夠」：兩種回應都是 2026-09-21 對 8085（--ctx-size 16384）實測來的

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def _server(monkeypatch, status, body):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: _Resp(status, body))


def _reply(content, finish="stop", usage=None):
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return body


def _call():
    return llm._call(HOST, [{"role": "user", "content": "x"}], {}, "local", "")


def test_提示超過上下文認得出來(monkeypatch):
    _server(monkeypatch, 400, {"error": {
        "code": 400, "type": "exceed_context_size_error",
        "message": "request (18403 tokens) exceeds the available context size (16384 tokens), "
                   "try increasing it",
        "n_prompt_tokens": 18403, "n_ctx": 16384}})
    with pytest.raises(llm.LlmContextFull, match="18403"):
        _call()


def test_回答寫到一半撞滿也算(monkeypatch):
    """提示 16284、上限 16384：回答寫了 100 個 token 就停在 length。"""
    _server(monkeypatch, 200, _reply('{"a": "天地玄', "length",
                                     {"prompt_tokens": 16284, "completion_tokens": 100}))
    with pytest.raises(llm.LlmContextFull):
        _call()


def test_其他錯誤不當成上下文不夠(monkeypatch):
    _server(monkeypatch, 500, {"error": {"code": 500, "message": "服務炸了"}})
    with pytest.raises(llm.LlmCallFailed) as e:
        _call()
    assert not isinstance(e.value, llm.LlmContextFull)


def test_用量是整段提示加回答(monkeypatch):
    """接著問只重算了 27 個 token 的那一次，prompt_tokens 回報的是整段 801。"""
    _server(monkeypatch, 200, _reply('{"a": "甲"}', usage={
        "prompt_tokens": 801, "completion_tokens": 7,
        "prompt_tokens_details": {"cached_tokens": 774}}))
    data, raw, used = _call()
    assert data == {"a": "甲"} and used == 808


def test_沒回報用量就是零(monkeypatch):
    _server(monkeypatch, 200, _reply('{"a": "甲"}'))
    assert _call()[2] == 0


# ---------------------------------------------------------------------------
# issue #6：接著問的時候，拿來判斷有沒有退步的數字要算整串，不能只看最後一則
# ---------------------------------------------------------------------------

# 第二批：個人資料與兩張示意圖在第一輪，這一輪只接了三個字、沒有重附圖
_CHAINED = [
    {"role": "system", "content": "系" * 600},
    {"role": "user", "content": [{"type": "text", "text": "個人資料" * 50}, _PAGE, _PAGE]},
    {"role": "assistant", "content": "{}"},
    {"role": "user", "content": [{"type": "text", "text": "第二批"}]},
]
_CHAINED_CHARS = 600 + 200 + 2 + 3


def test_log寫的是整串的長度與圖片(monkeypatch, caplog):
    """只看最後一則的話 log 印「圖片=0」，分不出是正確地沒有重附，還是圖片全掉了。"""
    _server(monkeypatch, 200, _reply('{"a": "甲"}', usage={
        "prompt_tokens": 5000, "completion_tokens": 7}))
    with caplog.at_level(logging.INFO, logger="backend.core.llm"):
        llm._call(HOST, _CHAINED, {}, "local", "配對")

    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("模型呼叫"))
    assert f"提示={_CHAINED_CHARS}字" in line
    assert "圖片=2（這一輪新附 3字、0張）" in line
    assert "第2輪" in line and "tokens=5000" in line


def test_截斷時說的是整段提示的長度(monkeypatch):
    """以前只算最後一則：實際送出一萬多 token，訊息卻說提示只有幾百——偏偏在「累積的
    對話就是原因」的時候。伺服器有報就用伺服器算的。"""
    _server(monkeypatch, 200, _reply('{"a": "天', "length",
                                     {"prompt_tokens": 16284, "completion_tokens": 100}))
    with pytest.raises(llm.LlmContextFull, match="約 16284 tokens"):
        llm._call(HOST, _CHAINED, {}, "local", "配對")


def test_截斷時伺服器沒報就用整串的字數估(monkeypatch):
    _server(monkeypatch, 200, _reply('{"a": "天', "length"))
    with pytest.raises(llm.LlmContextFull, match=f"約 {_CHAINED_CHARS // 2} tokens"):
        llm._call(HOST, _CHAINED, {}, "local", "配對")


def test_langfuse記整串的圖片與這一輪新附的(monkeypatch):
    seen = {}

    class _Generation:
        def update(self, **kw):
            pass

    class _Tracer:
        def start_as_current_generation(self, **kw):
            seen.update(kw["metadata"])
            return nullcontext(_Generation())

    monkeypatch.setattr(llm, "_tracer", lambda: _Tracer())
    _server(monkeypatch, 200, _reply('{"a": "甲"}'))
    llm._call(HOST, _CHAINED, {}, "local", "配對")
    assert seen["images"] == 2 and seen["images_new"] == 0


# ---------------------------------------------------------------------------
# 配對那一輪（filler.ask）：撞到的那一批在新的一串上重問，而不是整批丟掉
# ---------------------------------------------------------------------------

def _answer(schema):
    """照 schema 回一份合法的答案：有「無」就選「無」，沒有就選第一個。"""
    if schema.get("type") == "object":
        return {k: _answer(v) for k, v in schema["properties"].items()}
    vals = schema.get("enum") or [""]
    return filler.NONE if filler.NONE in vals else vals[0]


def _tight_server(calls):
    """上下文小到只放得下一批：對話裡已經有上一批，這一次就撞到。"""
    def call(host, messages, schema, model, label):
        turns = sum(m["role"] == "user" for m in messages)
        calls.append((label, turns, messages[-1]["content"]))
        if messages[0]["content"] == filler.SYSTEM and turns > 1:
            raise llm.LlmContextFull("這份文件太長，超出模型一次能讀的長度")
        return _answer(schema), "{}", 100
    return call


def test_撞到的那一批在新的一串上重問(monkeypatch, sample_form, profile):
    calls = []
    monkeypatch.setattr(llm, "_call", _tight_server(calls))
    monkeypatch.setattr(llm, "context_size", lambda host: 16384)
    doc, form, slots = filler.parse(sample_form)
    fields, pages = filler.usable_fields(form, profile), filler.render_pages(doc)
    first, second = filler._batches(slots)[:2]
    chat = llm.Chat(HOST, filler.SYSTEM)

    filler.ask(first, fields, pages, {}, chat)
    filler.ask(second, fields, pages, {}, chat)        # 以前這裡丟出例外，這一批整批作廢

    assert [turns for _label, turns, _content in calls] == [1, 2, 1]
    retry = calls[-1]
    assert "重問" in retry[0]
    texts = [p.get("text", "") for p in retry[2]]
    assert any(t.startswith("個人資料") for t in texts)  # 新的一串，個人資料重新附上
    assert any(p.get("type") == "image_url" for p in retry[2])


def test_先重開的話這一批要重組(monkeypatch, sample_form, profile):
    """start_over_if_long 重開之後，這一批要照新的一串重組。沿用原本組好的那份，
    送出去的就只剩位置清單：沒有個人資料、沒有示意圖，文法照樣逼模型挑一個，
    盲猜的值會一路寫進 .docx，不會有任何錯誤訊息。"""
    calls = []

    def big_reply(host, messages, schema, model, label):
        calls.append((sum(m["role"] == "user" for m in messages), messages[-1]["content"]))
        return _answer(schema), "{}", 9900          # 第一批問完，對話已經 9900

    monkeypatch.setattr(llm, "_call", big_reply)
    monkeypatch.setattr(llm, "context_size", lambda host: 10000)
    doc, form, slots = filler.parse(sample_form)
    fields, pages = filler.usable_fields(form, profile), filler.render_pages(doc)
    first, second = filler._batches(slots)[:2]
    chat = llm.Chat(HOST, filler.SYSTEM)

    filler.ask(first, fields, pages, {}, chat)
    filler.ask(second, fields, pages, {}, chat)        # 接上去會超過 10000：先重開

    turns, content = calls[-1]
    assert turns == 1                                  # 真的重開了
    assert any(p.get("text", "").startswith("個人資料") for p in content)
    assert any(p.get("type") == "image_url" for p in content)


def test_新的一串也塞不下就不重問(monkeypatch, sample_form, profile):
    """已經是新的一串還撞到，重開也一樣塞不下：丟出去交給 analyze 記一筆失敗就好。"""
    calls = []

    def always_full(host, messages, schema, model, label):
        calls.append(label)
        raise llm.LlmContextFull("太長")

    monkeypatch.setattr(llm, "_call", always_full)
    doc, form, slots = filler.parse(sample_form)
    fields, pages = filler.usable_fields(form, profile), filler.render_pages(doc)
    with pytest.raises(llm.LlmContextFull):
        filler.ask(filler._batches(slots)[0], fields, pages, {},
                   llm.Chat(HOST, filler.SYSTEM))
    assert len(calls) == 1


def test_撞到上下文之後不會每一批都失敗(monkeypatch, sample_form, profile, caplog):
    """issue #4 的情境走一整輪 analyze：接著問一律撞到。以前第一次撞到之後，
    後面每一批都接在同樣長的對話上再撞一次，配對那一輪幾乎全空。"""
    calls = []
    monkeypatch.setattr(llm, "_call", _tight_server(calls))
    monkeypatch.setattr(llm, "context_size", lambda host: 16384)
    with caplog.at_level(logging.WARNING, logger="backend.core.filler"):
        filler.analyze(sample_form, profile, host=HOST)

    assert not [r for r in caplog.records if "批問失敗" in r.getMessage()]
    assert sum(1 for label, _turns, _content in calls if label.startswith("配對")) > 1

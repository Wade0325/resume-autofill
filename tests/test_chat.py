"""接著問的那一串對話（`llm.Chat`）。

配對那一輪的幾批共用一串對話，個人資料與示意圖只在第一次用到時附上。省下來的是
llama-server 的提示快取，但也因此多了一種以前不存在的失敗方式：**某一批沒問成的時候，
「附過什麼」的帳不能算數**。算數的話，下一批送出去的只有位置清單——沒有個人資料、
沒有示意圖、連前文都沒有——而 JSON Schema 編成的文法照樣逼模型從欄位清單裡挑一個，
猜出來的值會一路寫進使用者的 .docx，不會有任何錯誤訊息。

這一組不需要模型：`llm._call` 換成假的。
"""
from __future__ import annotations

import pytest

from backend.core import llm


def _fake_call(answer="{}", fail=False):
    """代替 llm._call。fail=True 就模擬 llama-server 這次掛了。"""
    seen = []

    def call(host, messages, schema, model, label):
        seen.append([dict(m) for m in messages])
        if fail:
            raise llm.LlmCallFailed("模擬呼叫失敗")
        return {}, answer

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
    chat = llm.Chat("http://127.0.0.1:8099", "系統提示")

    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)
    user2, att2 = _compose(chat, "batch2", ["fields", "page0"])

    assert att1 == ["fields", "page0"]
    assert att2 == []                                  # 第二批什麼都不必再附
    assert user2 == [{"type": "text", "text": "batch2"}]


def test_沒問成就不算附過(monkeypatch):
    """這一組就是為了擋住那個錯誤：記帳寫在組訊息的時候，失敗時收不回來。"""
    monkeypatch.setattr(llm, "_call", _fake_call(fail=True))
    chat = llm.Chat("http://127.0.0.1:8099", "系統提示")

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
    chat = llm.Chat("http://127.0.0.1:8099", "系統提示")
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
    chat = llm.Chat("http://127.0.0.1:8099", "系統提示")

    user1, att1 = _compose(chat, "batch1", ["fields"])
    chat.ask(user1, {}, attached=att1)
    chat.ask([{"type": "text", "text": "batch2"}], {})

    assert len(call.seen[0]) == 2          # 第一次送的是 system＋user
    assert len(call.seen[1]) == 4          # 第二次才是 system＋user＋assistant＋user


def test_對話太長就重開一串(monkeypatch):
    monkeypatch.setattr(llm, "_call", _fake_call())
    chat = llm.Chat("http://127.0.0.1:8099", "系統提示")
    user1, att1 = _compose(chat, "batch1", ["fields", "page0"])
    chat.ask(user1, {}, attached=att1)

    chat.start_over_if_long(limit=0)                   # 一定超過
    assert chat.messages == [{"role": "system", "content": "系統提示"}]
    assert not chat.seen("fields")                     # 重開之後要再附一次
    assert not chat.seen("page0")


def test_圖片按實測的重量估(monkeypatch):
    """tokens() 是重開門檻的依據，圖片不能當成一般文字算。"""
    monkeypatch.setattr(llm, "_call", _fake_call())
    chat = llm.Chat("http://127.0.0.1:8099", "")
    chat.messages.append({"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,XX"}}]})
    assert chat.tokens() == 1300

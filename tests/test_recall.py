"""補漏那一輪（`filler.recall`）一次塞不下才切開（#13）。

以前一律一次問：空格最多的時候——配對那一輪失敗、表格又長——4 頁的長表格一次要問
89 格、提示 13175 token，超過上下文就整輪作廢。但也不能一開始就切：實測固定每 8 格
一批，緊急聯絡人的電話填成本人的手機、同一項資料被好幾格挑中（見 recall 的說明）。
所以塞得下就照舊一次問完，只有伺服器說塞不下才對半切。

這一組不需要模型：`llm.ask`（或 `_recall_batch`）換成假的。虛構表格在「前面幾輪什麼
都沒填到」時有 45 格要補。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from backend.core import filler, llm


def _form(sample_form, profile):
    doc, form, slots = filler.parse(sample_form)
    return slots, filler.usable_fields(form, profile), filler.render_pages(doc)


def _server(calls, limit=10_000):
    """假的 llama-server：一次問超過 limit 格就回「上下文不夠」，否則每格都挑第一個候選。"""
    def ask(host, system, user, schema, model="local", label=""):
        calls.append({"n": len(schema["properties"]),
                      "images": {p["image_url"]["url"] for p in user
                                 if p.get("type") == "image_url"}})
        if len(schema["properties"]) > limit:
            raise llm.LlmContextFull("這份文件太長，超出模型一次能讀的長度")
        return {sid: {"pick": item["properties"]["pick"]["enum"][0], "answers": "是"}
                for sid, item in schema["properties"].items()}
    return ask


def _tried(monkeypatch, limit=10_000, fail=()):
    """攔下每一次交給 _recall_batch 的那一段。超過 limit 格就當成塞不下；
    第幾次（從 1 數）在 fail 裡就丟別的失敗。"""
    tried = []

    def spy(todo, fields, pages, host, model):
        tried.append(todo)
        if len(tried) in fail:
            raise llm.LlmCallFailed("模型 10 分鐘內沒有回應")
        if len(todo) > limit:
            raise llm.LlmContextFull("這份文件太長，超出模型一次能讀的長度")
        return {s.id: cands[0] for s, cands in todo}

    monkeypatch.setattr(filler, "_recall_batch", spy)
    return tried


def test_塞得下就一次問完(monkeypatch, sample_form, profile):
    """跟以前一模一樣：送出去的東西一字不差（三份考題的 payload 雜湊另外比過）。"""
    calls = []
    monkeypatch.setattr(llm, "ask", _server(calls))
    slots, fields, pages = _form(sample_form, profile)
    got = filler.recall(slots, {}, fields, pages)
    assert [c["n"] for c in calls] == [45]
    assert len(got) == 45


def test_塞不下才對半切(monkeypatch, sample_form, profile):
    calls = []
    monkeypatch.setattr(llm, "ask", _server(calls, limit=12))
    slots, fields, pages = _form(sample_form, profile)
    got = filler.recall(slots, {}, fields, pages)

    assert calls[0]["n"] == 45                        # 先照舊一次問
    assert sum(c["n"] for c in calls if c["n"] <= 12) == 45   # 問成的那幾段剛好涵蓋每一格
    assert len(got) == 45


def test_切在格子之間(monkeypatch, sample_form, profile):
    """「優點：」「缺點：」拆到兩邊，模型看不到彼此就容易錯位。"""
    tried = _tried(monkeypatch, limit=12)
    slots, fields, pages = _form(sample_form, profile)
    filler.recall(slots, {}, fields, pages)

    owner = {}
    for n, part in enumerate(p for p in tried if len(p) <= 12):
        for s, _cands in part:
            assert owner.setdefault(s.addr, n) == n


def test_對半切的那一刀():
    a, b, c = (SimpleNamespace(addr=x) for x in "abc")
    todo = [(a, []), (b, []), (b, []), (b, []), (c, [])]
    assert filler._halve(todo) == 1                   # 正中間（2）會拆開 b 那一格，退到最近的邊界
    todo = [(a, []), (a, []), (b, []), (c, []), (c, []), (c, [])]
    assert filler._halve(todo) == 3
    same = [(a, [])] * 5
    assert filler._halve(same) == 2                   # 整段同一格就從中間切


def test_一格也塞不下就略過(monkeypatch, sample_form, profile):
    tried = _tried(monkeypatch, limit=0)
    slots, fields, pages = _form(sample_form, profile)
    assert filler.recall(slots, {}, fields, pages) == {}
    assert len(tried) < 2 * 45                        # 切到一格就停，不會沒完沒了


def test_其他失敗不切(monkeypatch, sample_form, profile):
    """逾時、服務出錯切了也一樣：照舊丟給 analyze 記一筆，不要白白多問好幾次。"""
    tried = _tried(monkeypatch, fail={1})
    slots, fields, pages = _form(sample_form, profile)
    with pytest.raises(llm.LlmCallFailed):
        filler.recall(slots, {}, fields, pages)
    assert len(tried) == 1


def test_切開之後一段失敗另一段照收(monkeypatch, sample_form, profile, caplog):
    tried = _tried(monkeypatch, limit=30, fail={2})   # 第 2 次是切開後的前半段
    slots, fields, pages = _form(sample_form, profile)
    with caplog.at_level(logging.WARNING, logger="backend.core.filler"):
        got = filler.recall(slots, {}, fields, pages)

    first_half = {s.id for s, _cands in tried[1]}
    assert got and not first_half & set(got)
    assert len(got) == 45 - len(first_half)
    assert any("補漏有一段問失敗" in r.getMessage() for r in caplog.records)


def test_每一段只附自己那幾格所在的示意圖(monkeypatch, sample_form, profile):
    """前半的格子畫在第 0 張、後半在第 1 張：切開之後，前後兩段各附各的。"""
    slots, fields, _pages = _form(sample_form, profile)
    addrs = list(dict.fromkeys(s.addr for s in slots))
    half = len(addrs) // 2
    pages = filler.Pages(urls=["data:,p0", "data:,p1"],
                         page_of={a: (0, 0) if i < half else (1, 1) for i, a in enumerate(addrs)})
    calls = []
    monkeypatch.setattr(llm, "ask", _server(calls, limit=12))
    filler.recall(slots, {}, fields, pages)

    ok = [c["images"] for c in calls if c["n"] <= 12]
    assert {"data:,p0"} in ok and {"data:,p1"} in ok


def test_沒有空格就不問(monkeypatch, sample_form, profile):
    tried = _tried(monkeypatch)
    slots, fields, pages = _form(sample_form, profile)
    assert filler.recall(slots, {s.id: "x" for s in slots}, fields, pages) == {}
    assert tried == []

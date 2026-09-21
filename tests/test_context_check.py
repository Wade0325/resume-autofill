"""模型起來之後的上下文下限檢查。

太小的話一定要在「切換模型」當下就講：不講的話，使用者要等到丟進一份長表格、
分析跑到一半才失敗，而且訊息跟他做的事看起來毫無關係。

這一組不需要模型：`/props` 的回應換成假的。
"""
from __future__ import annotations

import logging

import pytest

from backend import model_manager
from backend.core import llm


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _props(monkeypatch, payload):
    monkeypatch.setattr(llm.requests, "get", lambda *a, **k: _Resp(payload))


def test_讀得到伺服器回報的上下文(monkeypatch):
    _props(monkeypatch, {"default_generation_settings": {"n_ctx": 16384}})
    assert llm.context_size("http://127.0.0.1:8099") == 16384


@pytest.mark.parametrize("payload", [
    {},                                          # 舊版 server 沒這個欄位
    {"default_generation_settings": {}},
    {"default_generation_settings": {"n_ctx": None}},
])
def test_問不到就回零(monkeypatch, payload):
    _props(monkeypatch, payload)
    assert llm.context_size("http://127.0.0.1:8099") == 0


def test_連不上也回零而不是炸開(monkeypatch):
    def boom(*a, **k):
        raise OSError("連不上")
    monkeypatch.setattr(llm.requests, "get", boom)
    assert llm.context_size("http://127.0.0.1:8099") == 0


def _check(monkeypatch, caplog, n_ctx):
    """跑一次檢查，回傳 (使用者看得到的訊息, 開發者 log)。"""
    monkeypatch.setattr(llm, "context_size", lambda host: n_ctx)
    with caplog.at_level(logging.INFO):
        model_manager._check_context("虛構模型")
    user = [r.getMessage() for r in caplog.records if r.name == "action"]
    dev = [r.getMessage() for r in caplog.records if r.name != "action"]
    return user, dev


def test_太小就告訴使用者(monkeypatch, caplog):
    user, dev = _check(monkeypatch, caplog, model_manager.CTX_FLOOR - 1)
    assert len(user) == 1
    assert "填不完" in user[0]
    # 白話短句：數字與環境變數留在開發者 log，不往使用者臉上丟
    assert "RESUME_AUTOFILL_LLM_CTX" not in user[0]
    assert str(model_manager.CTX_FLOOR) not in user[0]
    assert any("RESUME_AUTOFILL_LLM_CTX" in m for m in dev)


def test_偏小只寫開發者log不吵使用者(monkeypatch, caplog):
    user, dev = _check(monkeypatch, caplog, model_manager.CTX_FLOOR + 1)
    assert user == []
    assert any("偏小" in m for m in dev)


def test_夠用就什麼都不說(monkeypatch, caplog):
    user, dev = _check(monkeypatch, caplog, model_manager.CTX_COMFORTABLE)
    assert user == []
    assert not any("偏小" in m or "太小" in m for m in dev)


def test_問不到就不判斷(monkeypatch, caplog):
    """寧可不說，也不要嚇到其實沒事的人。"""
    user, dev = _check(monkeypatch, caplog, 0)
    assert user == []
    assert any("略過檢查" in m for m in dev)


def test_門檻蓋得住實測的最壞情況():
    """三份考題實際峰值 7637；很厚的履歷配 4 頁表格推算約 11600。

    改這兩個常數的人要一起改上面那段推導——數字是量出來的，不是抓的。
    """
    assert model_manager.CTX_FLOOR >= 7637
    assert model_manager.CTX_COMFORTABLE >= 11600

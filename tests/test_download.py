"""模型下載的校驗：sha256 要從 Hugging Face 轉址那一跳拿。

requests 會一路跟到 CDN，最後那一跳的 ETag 是 Xet 雜湊——同樣是 64 位十六進位，
卻不是檔案的 sha256。以前拿它比對，每個模型下載完都被當成壞檔刪掉，重下也一樣，
新使用者永遠裝不起來。
"""
from __future__ import annotations

import hashlib

import pytest
import requests

from backend import model_manager

CONTENT = b"fake gguf content for tests"
SHA = hashlib.sha256(CONTENT).hexdigest()
XET = hashlib.sha256(b"xet hash of the same file").hexdigest()   # 長得像 sha256，但不是
URL = "https://huggingface.co/fake/repo/resolve/main/fake.gguf"


def _response(status: int, headers: dict, history=(), body: bytes = b"") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers)
    r.history = list(history)
    r.url = URL
    r._content = body
    r._content_consumed = True
    return r


def _from_hub(sha: str, body: bytes = CONTENT) -> requests.Response:
    """照 huggingface.co 的實際樣子：302 帶 X-Linked-Etag，CDN 回 200 帶自己的 ETag。"""
    hop = _response(302, {"Location": "https://cas-bridge.xethub.hf.co/fake",
                          "X-Linked-Etag": f'"{sha}"', "ETag": '"0123abcd"'})
    return _response(200, {"ETag": f'"{XET}"', "Content-Length": str(len(body))},
                     history=[hop], body=body)


def test_sha_comes_from_the_redirect():
    assert model_manager._expected_sha(_from_hub(SHA)) == SHA


def test_cdn_etag_alone_is_not_trusted():
    """一般網站或 CDN 的 ETag 就算是 64 位十六進位，也不能當 sha256。"""
    assert model_manager._expected_sha(_response(200, {"ETag": f'"{XET}"'})) == ""


def test_download_from_hub_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(model_manager.requests, "get", lambda *a, **k: _from_hub(SHA))
    dest = tmp_path / "fake.gguf"
    model_manager._fetch(URL, dest, "虛構模型", track=False)
    assert dest.read_bytes() == CONTENT
    assert not model_manager._part_of(dest).exists()


def test_corrupt_download_is_dropped(monkeypatch, tmp_path):
    """真的對不上的還是要擋：壞檔留著只會一直續傳到同一個壞結果。"""
    other = hashlib.sha256(b"what the hub says it should be").hexdigest()
    monkeypatch.setattr(model_manager.requests, "get", lambda *a, **k: _from_hub(other))
    dest = tmp_path / "fake.gguf"
    with pytest.raises(model_manager.ModelError):
        model_manager._fetch(URL, dest, "虛構模型", track=False)
    assert not dest.exists()
    assert not model_manager._part_of(dest).exists()

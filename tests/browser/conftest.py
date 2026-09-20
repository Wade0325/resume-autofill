"""瀏覽器測試的共用準備。

這一組需要三樣東西：playwright、一份 chromium、以及 build 好的 `frontend/dist`。
缺任何一樣就整組跳過——沒裝這些的人（或 CI）照樣跑得完其他測試。

跑法：`npm --prefix frontend run build` 之後 `pytest -m browser`。

後端另外開一個行程跑（不是 TestClient）：畫面要真的連得上 HTTP，
而且 dist 是後端在 serve 的。它有自己的暫存資料夾，跟其他測試互不干擾。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DIST = ROOT / "frontend" / "dist" / "index.html"

pytest.importorskip("playwright", reason="沒裝 playwright")
pytestmark = pytest.mark.browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium(pw):
    """先用 playwright 自己找得到的，找不到再試已知的安裝位置。"""
    for kwargs in ({}, {"executable_path": str(
            Path.home() / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe")}):
        try:
            return pw.chromium.launch(**kwargs)
        except Exception:
            continue
    pytest.skip("找不到可用的 chromium（跑 playwright install chromium）")


@pytest.fixture(scope="session")
def live_server():
    """開一個真的後端，回傳 (網址, 那一份暫存資料夾)。"""
    if not DIST.exists():
        pytest.skip("frontend/dist 還沒 build（npm --prefix frontend run build）")

    home = Path(tempfile.mkdtemp(prefix="resume_autofill_browser_"))
    port = _free_port()
    env = {**os.environ,
           "RESUME_AUTOFILL_HOME": str(home),
           "RESUME_AUTOFILL_LLM_HOST": "http://127.0.0.1:8099",
           "RESUME_AUTOFILL_AUTOSTART": "0"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = f"http://127.0.0.1:{port}"
    try:
        import requests
        for _ in range(120):
            try:
                if requests.get(base + "/api/health", timeout=3).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            pytest.fail("後端沒有起來")
        yield base, home
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = _chromium(pw)
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server):
    """一頁乾淨的瀏覽器，附帶「有沒有跳出頁面錯誤」的檢查。"""
    base, _home = live_server
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(base + "/logs")          # 先進一個輕量的頁面，才有 origin 可以寫 sessionStorage
    yield p
    ctx.close()
    assert not errors, f"畫面丟出錯誤：{errors}"


@pytest.fixture
def seed(live_server):
    """在那個後端的資料夾裡生一份工作，不必真的跑模型。

    換個行程做：後端有自己的資料夾，而 `backend.config` 在 import 當下就把路徑定死了，
    同一個行程裡改不回來。細節見 `_seed.py`。
    """
    _base, home = live_server
    sys.path.insert(0, str(ROOT / "tools"))
    import make_sample

    form = home / "sample_form.docx"
    if not form.exists():
        make_sample.build(str(form))

    def _seed(filename="虛構公司表格.docx", **opts):
        opts["filename"] = filename
        out = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "_seed.py"),
             str(home), str(form), json.dumps(opts, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert out.returncode == 0, out.stdout + out.stderr
        return out.stdout.strip().splitlines()[-1]

    _seed.form = form
    return _seed


@pytest.fixture
def seed_import(seed, live_server):
    """塞一筆讀完的匯入紀錄。沒有模型也測得到讀完之後畫面長怎樣。"""
    _base, home = live_server

    def _seed_import(**opts):
        opts["kind"] = "import"
        out = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "_seed.py"),
             str(home), str(seed.form), json.dumps(opts, ensure_ascii=False)],
            capture_output=True, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert out.returncode == 0, out.stdout + out.stderr
        return out.stdout.strip().splitlines()[-1]

    return _seed_import


@pytest.fixture
def slots_of():
    def _slots(form_path):
        from backend.core import filler

        _doc, _f, slots = filler.parse(form_path)
        return slots
    return _slots

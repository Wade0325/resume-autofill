"""版本號以 `backend.__version__` 為準，其他地方的副本要跟它一致。

以前四處不一：pyproject 寫 0.1.0、前端 0.0.0、啟動器沒有，畫面上也看不到——
使用者回報問題時說不出自己用的是哪一版，打包出來的檔名也對不上。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import backend

ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_版本號是三段數字():
    assert re.fullmatch(r"\d+\.\d+\.\d+", backend.__version__)


def test_pyproject跟著一致():
    project = _read("pyproject.toml").split("[project]", 1)[1].split("\n[", 1)[0]
    assert re.search(r'^version = "([^"]+)"', project, re.M).group(1) == backend.__version__


def test_前端跟著一致():
    assert json.loads(_read("frontend/package.json"))["version"] == backend.__version__
    lock = json.loads(_read("frontend/package-lock.json"))
    assert lock["version"] == lock["packages"][""]["version"] == backend.__version__


def test_啟動器跟著一致():
    csproj = _read("launcher/ResumeAutoFill.Launcher/ResumeAutoFill.Launcher.csproj")
    assert re.search(r"<Version>([^<]+)</Version>", csproj).group(1) == backend.__version__


def test_health回報版本而且啟動器認得出來(client):
    """啟動器靠 `"api":"ok"` 認出是自己的後端（Program.IsOurBackendHealthy），多了欄位也要還在。"""
    r = client.get("/api/health")
    assert r.json()["version"] == backend.__version__
    assert '"api":"ok"' in r.text

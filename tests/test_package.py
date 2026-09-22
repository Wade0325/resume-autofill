"""發佈包的檢查：打包的 Python 相依鎖死而且涵蓋 pyproject、.ps1 的編碼。"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def _pins() -> dict:
    pins = {}
    for line in (ROOT / "scripts" / "package-requirements.txt").read_text(
            encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, _, version = line.partition("==")
        assert version, f"打包的相依要鎖死版本：{line}"
        pins[canonicalize_name(name)] = version
    return pins


def _pyproject_deps() -> list:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.M | re.S).group(1)
    return re.findall(r'^\s*"([^"]+)"', block, re.M)


def test_打包的相依涵蓋pyproject而且符合版本下限():
    """鎖的版本是跑過測試的那一套；pyproject 多了一項、或下限往上調，這份要跟著改。"""
    pins = _pins()
    for raw in _pyproject_deps():
        req = Requirement(raw)
        version = pins.get(canonicalize_name(req.name))
        assert version, f"{req.name} 沒有打包進去"
        assert req.specifier.contains(version), f"{req.name}=={version} 不符合 {req.specifier}"


def test_ps1都存成UTF8加BOM():
    """PowerShell 5.1 讀沒有 BOM 的檔案會用 ANSI：中文全變亂碼，而且直接 ParserError。"""
    # 用相對路徑判斷：worktree 本身就在 .claude/worktrees/ 底下。
    # .venv 裡是第三方套件自帶的（playwright 的 install_media_pack.ps1），不歸我們管
    skip = {"node_modules", ".claude", ".venv", "dist", "build"}
    files = [p for p in ROOT.glob("**/*.ps1") if not skip & set(p.relative_to(ROOT).parts)]
    assert files
    for p in files:
        assert p.read_bytes().startswith(b"\xef\xbb\xbf"), f"{p.relative_to(ROOT)} 沒有 BOM"

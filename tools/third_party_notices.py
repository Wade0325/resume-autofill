"""產生發佈包的 THIRD-PARTY-NOTICES.txt：隨附的第三方元件、版本、授權與授權全文。

    python tools/third_party_notices.py <打包進去的 site-packages> <前端 source map 目錄> <輸出檔>

build-package.ps1 會呼叫它。三種來源：
- Python 套件：直接讀打包進去的 site-packages，版本就是實際隨附的那一版，授權全文取自各自的
  .dist-info
- 前端：只列**實際打包進 app\\frontend\\dist** 的套件——由 source map 引用到的 node_modules 決定。
  package.json 的相依裡有一大半是 Vite 外掛、Tailwind 編譯器、各平台的執行檔這類建置工具，
  不會出現在成品裡。Tailwind 產生的樣式會進成品，另外列
- llama.cpp、.NET runtime：授權全文存在 scripts/licenses/（照 GitHub 上對應版本的原文）；
  NVIDIA CUDA runtime、Python 本體附條款出處
- Playwright 自帶的 Node.js 與驅動程式：不在 .dist-info 裡，授權全文取自 playwright\\driver
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES = ROOT / "scripts" / "licenses"
NODE = ROOT / "frontend" / "node_modules"
RULE = "=" * 78

FIXED = [
    ("llama.cpp（bin\\llama-server.exe 與 ggml*、llama*、mtmd 的 DLL）", "b10153", "MIT",
     "https://github.com/ggml-org/llama.cpp", LICENSES / "llama.cpp-LICENSE.txt"),
    ("NVIDIA CUDA 13 runtime（bin\\cudart64_13.dll、cublas64_13.dll、cublasLt64_13.dll）",
     "13", "NVIDIA CUDA Toolkit EULA（可轉散發元件）",
     "https://docs.nvidia.com/cuda/eula/index.html", None),
    ("OpenMP 執行階段（bin\\libomp140.x86_64.dll，Microsoft Visual C++，隨 llama.cpp 官方 "
     "Windows 版一起發佈）", "", "Microsoft Visual C++ 可轉散發元件", "", None),
    ("Python（app\\runtime，embeddable package）", "3.11.9", "Python Software Foundation License",
     "全文見 app\\runtime\\LICENSE.txt", None),
    (".NET runtime（內嵌於 ResumeAutoFill.exe，單檔自足）", "10", "MIT",
     "https://github.com/dotnet/runtime（其餘元件見該 repo 的 THIRD-PARTY-NOTICES.TXT）",
     LICENSES / "dotnet-runtime-LICENSE.txt"),
]


def _python(site: Path):
    rows = []
    for dist in sorted(metadata.distributions(path=[str(site)]),
                       key=lambda d: d.metadata["Name"].lower()):
        m = dist.metadata
        lic = (m.get("License-Expression") or "").strip()
        if not lic:
            raw = (m.get("License") or "").strip()
            lic = raw if raw and len(raw) < 60 and "\n" not in raw else ""
        if not lic:
            lic = "；".join(c.split("::")[-1].strip() for c in m.get_all("Classifier") or []
                           if c.startswith("License ::")) or "見授權全文"
        texts = [f for f in dist.files or []
                 if re.match(r"(licen[cs]e|copying|notice|authors)", Path(str(f)).name, re.I)
                 and ".dist-info" in str(f)]
        body = "\n\n".join(Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace")
                           .strip() for f in texts)
        rows.append((m["Name"], dist.version, lic, m.get("Home-page") or "", body))
    return rows


def _playwright_driver(site: Path):
    """Playwright（網頁填寫用）自帶的 Node.js 與驅動程式。它們不在 .dist-info 裡，要另外列。"""
    driver = site / "playwright" / "driver"
    if not driver.exists():
        return []
    try:
        node = subprocess.run([str(driver / "node.exe"), "--version"], capture_output=True,
                              text=True, timeout=30).stdout.strip().lstrip("v")
    except OSError:
        node = ""
    pkg = driver / "package"
    notices = "\n\n".join((pkg / n).read_text(encoding="utf-8", errors="replace").strip()
                          for n in ("LICENSE", "NOTICE", "ThirdPartyNotices.txt")
                          if (pkg / n).exists())
    return [
        ("Node.js（app\\runtime\\Lib\\site-packages\\playwright\\driver\\node.exe，"
         "Playwright 的執行環境）", node, "MIT（內含元件的授權見全文）", "https://nodejs.org",
         (driver / "LICENSE").read_text(encoding="utf-8", errors="replace").strip()),
        ("Playwright 驅動程式（app\\runtime\\Lib\\site-packages\\playwright\\driver\\package）",
         _dist_version(site, "playwright"), "Apache-2.0", "https://github.com/microsoft/playwright",
         notices),
    ]


def _dist_version(site: Path, name: str) -> str:
    return next((d.version for d in metadata.distributions(path=[str(site)])
                 if d.metadata["Name"].lower() == name), "")


def _bundled_js(maps: Path):
    names = set()
    for f in maps.rglob("*.map"):
        for src in json.loads(f.read_text(encoding="utf-8")).get("sources", []):
            hit = re.search(r"node_modules/((?:@[^/]+/)?[^/]+)/", src)
            if hit:
                names.add(hit.group(1))
    names.add("tailwindcss")                 # 產生的樣式（含 preflight）打包在 CSS 裡
    rows = []
    for name in sorted(names):
        pkg = json.loads((NODE / name / "package.json").read_text(encoding="utf-8"))
        texts = [f for f in (NODE / name).iterdir()
                 if re.match(r"(licen[cs]e|copying|notice)", f.name, re.I) and f.is_file()]
        body = "\n\n".join(t.read_text(encoding="utf-8", errors="replace").strip()
                           for t in texts)
        lic = pkg.get("license") or "見授權全文"
        rows.append((name, pkg.get("version", ""), lic if isinstance(lic, str) else str(lic),
                     pkg.get("homepage") or "", body))
    return rows


def main(site: Path, maps: Path, out: Path) -> None:
    py, js = _python(site), _bundled_js(maps)
    fixed = [(n, v, lic, url, p.read_text(encoding="utf-8").strip() if p else "")
             for n, v, lic, url, p in FIXED] + _playwright_driver(site)
    lines = ["Resume AutoFill 隨附的第三方元件", RULE,
             "本程式本身以 MIT 授權發佈（見 LICENSE.txt）。以下是發佈包裡一起散發的第三方元件、",
             "版本與授權；授權全文附在後面。AI 模型不隨附：由使用者在程式內自 Hugging Face 下載，",
             "授權以該模型頁面為準。網頁填寫用的是系統內建的 Edge（或 Chrome），瀏覽器不隨附。", ""]
    sections = [("執行檔與執行階段", fixed),
                ("Python 套件（app\\runtime\\Lib\\site-packages）", py),
                ("前端（打包進 app\\frontend\\dist 的 JavaScript 與 CSS）", js)]
    for title, rows in sections:
        lines += [f"【{title}】"]
        lines += [f"  - {n} {v}　{lic}" + (f"　{url}" if url else "") for n, v, lic, url, _ in rows]
        lines.append("")
    lines += ["", "授權全文", RULE]
    for _title, rows in sections:
        for n, v, _lic, _url, body in rows:
            if body:
                lines += ["", f"--- {n} {v} ".ljust(78, "-"), "", body]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(f"{out.name}：執行檔 {len(fixed)}、Python {len(py)}、前端 {len(js)} 項")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))

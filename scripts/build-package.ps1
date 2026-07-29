<#
    打包成可發佈的資料夾／zip：

        .\scripts\build-package.ps1            # 組出 dist\Resume_AutoFill\
        .\scripts\build-package.ps1 -Zip       # 另外壓成 dist\Resume_AutoFill.zip
        .\scripts\build-package.ps1 -Quick     # 跳過前端重建與 bin 複製（迭代測試用）

    產物結構（使用者拿到的樣子）：
        Resume_AutoFill\
          ResumeAutoFill.exe    C# 啟動器（單檔自足）
          app\                  後端原始碼＋前端頁面＋內嵌 Python
          bin\                  llama-server 與 CUDA DLL
          models\ input\ output\
          README.txt

    後端不凍結：內嵌官方 embeddable Python＋site-packages，
    怎麼開發就怎麼跑，沒有 PyInstaller 的隱藏相依與防毒誤判問題。
    注意：embeddable 版本必須跟開發用 Python 同 minor 版（二進位套件才相容）。
#>
param(
    [switch]$Zip,
    [switch]$Quick
)
$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist\Resume_AutoFill"
$app  = Join-Path $dist "app"
$cache = Join-Path $root "build"

$pyVersion = "3.11.9"
$embedZip  = "python-$pyVersion-embed-amd64.zip"
$embedUrl  = "https://www.python.org/ftp/python/$pyVersion/$embedZip"
$deps = @("fastapi", "uvicorn", "python-multipart", "python-docx",
          "requests", "pypdfium2", "pillow")

# ---- 0. 清掉上一次的產物 ----
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force $app | Out-Null
New-Item -ItemType Directory -Force $cache | Out-Null

# ---- 1. 前端 ----
if (-not $Quick) {
    Write-Host "== 前端 build =="
    Push-Location (Join-Path $root "frontend")
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "前端 build 失敗" }
    Pop-Location
}
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    throw "frontend\dist 不存在，先跑 npm run build"
}

# ---- 2. 後端原始碼＋前端頁面 ----
Write-Host "== 複製後端與前端 =="
Copy-Item (Join-Path $root "run_backend.py") $app
Copy-Item (Join-Path $root "backend") (Join-Path $app "backend") -Recurse
Get-ChildItem (Join-Path $app "backend") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $app "frontend") | Out-Null
Copy-Item (Join-Path $root "frontend\dist") (Join-Path $app "frontend\dist") -Recurse

# ---- 3. 內嵌 Python ----
Write-Host "== 內嵌 Python $pyVersion =="
$embedCache = Join-Path $cache $embedZip
if (-not (Test-Path $embedCache)) {
    Invoke-WebRequest $embedUrl -OutFile $embedCache
}
$runtime = Join-Path $app "runtime"
Expand-Archive $embedCache $runtime
# _pth 決定 sys.path：runtime 自身、app\（backend 套件）、site-packages。
# 不 import site——embeddable 的設計就是路徑全顯式，環境不受機器污染
@"
python311.zip
.
..
Lib\site-packages
"@ | Out-File (Join-Path $runtime "python311._pth") -Encoding ascii

Write-Host "== 安裝相依套件 =="
$site = Join-Path $runtime "Lib\site-packages"
# Microsoft Store 版 Python 的 pip 預設 --user，跟 --target 互斥，明確關掉
$env:PIP_USER = "false"
python -m pip install --target $site --no-warn-script-location --quiet @deps
if ($LASTEXITCODE -ne 0) { throw "pip install 失敗" }

# ---- 4. C# 啟動器 ----
Write-Host "== 啟動器 publish =="
$proj = Join-Path $root "launcher\ResumeAutoFill.Launcher"
dotnet publish $proj -c Release -o (Join-Path $cache "launcher-publish") --nologo -v q
if ($LASTEXITCODE -ne 0) { throw "dotnet publish 失敗" }
Copy-Item (Join-Path $cache "launcher-publish\ResumeAutoFill.exe") $dist

# ---- 5. llama-server 與資料夾骨架 ----
if (-not $Quick) {
    Write-Host "== 複製 llama.cpp（含 CUDA DLL，約 700 MB）=="
    Copy-Item (Join-Path $root "bin") (Join-Path $dist "bin") -Recurse
}
foreach ($d in "models", "input", "output", "data") {
    New-Item -ItemType Directory -Force (Join-Path $dist $d) | Out-Null
}
@"
Resume AutoFill
================
1. 雙擊 ResumeAutoFill.exe，瀏覽器會自動開啟操作介面。
2. 首次使用：點右上角「模型未啟動」→ 下載 Qwen3.5-9B（約 6 GB）→ 啟動。
3. 建議規格：NVIDIA 顯卡 8 GB VRAM。無獨顯也能跑，但速度會慢很多。
4. 選裝 LibreOffice 可支援 .doc 舊格式與排版預覽。
5. 個人資料只存在本資料夾的 data\ 裡，不會上傳；刪掉整個資料夾即完整移除。
結束程式：工作列右下角系統匣圖示 → 右鍵 → 結束。
"@ | Out-File (Join-Path $dist "README.txt") -Encoding utf8

# ---- 6. zip ----
if ($Zip) {
    Write-Host "== 壓縮 =="
    $zipPath = Join-Path $root "dist\Resume_AutoFill.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path $dist -DestinationPath $zipPath
}

Write-Host "完成 → $dist"

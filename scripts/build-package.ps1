<#
    打包成可發佈的資料夾與 zip：

        .\scripts\build-package.ps1            # 組出 dist\Resume_AutoFill\
        .\scripts\build-package.ps1 -Zip       # 另外壓成 dist\Resume_AutoFill-v<版本>.zip 與 SHA256SUMS.txt
        .\scripts\build-package.ps1 -Quick     # 跳過前端重建與 bin 複製（迭代測試用）
        .\scripts\build-package.ps1 -Bin D:\somewhere\bin   # llama-server 放在別處時（例如在 worktree 裡打包）

    產物結構（使用者拿到的樣子）：
        Resume_AutoFill\
          ResumeAutoFill.exe          C# 啟動器（單檔自足）
          app\                        後端原始碼＋前端頁面＋內嵌 Python
          bin\                        llama-server 與它要的 DLL（含 CUDA 13 runtime）
          models\ data\               AI 模型與個人資料（一開始是空的）
          README.txt  LICENSE.txt  THIRD-PARTY-NOTICES.txt

    版本號取自 backend\__init__.py：檔名與 README.txt 都用它。
    後端不凍結：內嵌官方 embeddable Python＋site-packages，
    怎麼開發就怎麼跑，沒有 PyInstaller 的隱藏相依與防毒誤判問題。
    Python 套件照 scripts\package-requirements.txt 的鎖定版本裝（--no-deps）：發出去的跟測過的一樣。
    注意：embeddable 版本必須跟開發用 Python 同 minor 版（二進位套件才相容）。
#>
param(
    [switch]$Zip,
    [switch]$Quick,
    [string]$Bin = ""
)
$ErrorActionPreference = "Stop"

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # 原生程式（npm、pip、dotnet、tar）一律經過這裡，只看結束碼。PowerShell 5.1 在輸出被
    # 重導時，會把它們寫到 stderr 的每一行（npm 的警告、pip 的版本提示）當成錯誤，配上
    # Stop 就整支腳本中斷——指令明明成功了，打包卻停在半路
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$What 失敗（結束碼 $LASTEXITCODE）" }
}

# 中文 Windows 的系統編碼是 cp950：舊版 pip 會用它讀需求檔，碰到中文註解就當場炸掉
$env:PYTHONUTF8 = "1"

$root = Split-Path $PSScriptRoot -Parent
if (-not $Bin) { $Bin = Join-Path $root "bin" }
$distRoot = Join-Path $root "dist"
$dist = Join-Path $distRoot "Resume_AutoFill"
$app  = Join-Path $dist "app"
$cache = Join-Path $root "build"

$init = Get-Content (Join-Path $root "backend\__init__.py") -Raw -Encoding UTF8
$version = [regex]::Match($init, '__version__ = "([^"]+)"').Groups[1].Value
if (-not $version) { throw "backend\__init__.py 裡找不到 __version__" }

$pyVersion = "3.11.9"
$embedZip  = "python-$pyVersion-embed-amd64.zip"
$embedUrl  = "https://www.python.org/ftp/python/$pyVersion/$embedZip"
# 下載下來的檔案要跟這個一致才用（快取裡的也一樣）：每次打包都是同一份、而且確實是
# python.org 發的。這個值取自一份 MD5 跟 python.org 下載頁公布的 6d9aa085…17c4 相符的下載
$embedSha256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"

# 開發用的 Python：優先用 repo 根目錄的 .venv（跑測試與研究迴圈的就是它），沒有才用 PATH 上的
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$devMinor = & $python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($devMinor -ne ($pyVersion -replace '\.\d+$', '')) {
    throw "開發用 Python 是 $devMinor、內嵌版是 $pyVersion：二進位套件不相容"
}

# llama-server 與它要的 DLL。bin\ 裡還有 llama.cpp 的其他工具（llama-cli、llama-bench……）
# 以及舊的空資料夾，用不到就不帶；缺任何一個就停下來，不要發出一包開不了模型的東西
$binFiles = @(
    "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll", "mtmd.dll",
    "ggml.dll", "ggml-base.dll", "ggml-cuda.dll",
    "cudart64_13.dll", "cublas64_13.dll", "cublasLt64_13.dll", "libomp140.x86_64.dll"
)

Write-Host "== Resume AutoFill v$version =="

# ---- 0. 清掉上一次的產物 ----
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force $app | Out-Null
New-Item -ItemType Directory -Force $cache | Out-Null

# ---- 1. 前端 ----
$maps = Join-Path $cache "frontend-maps"
if (-not $Quick) {
    Write-Host "== 前端 build =="
    Push-Location (Join-Path $root "frontend")
    try {
        Invoke-Native "npm ci" { npm ci --no-audit --no-fund --loglevel=error }
        Invoke-Native "前端 build" { npm run build }
        # 另外帶 source map 建一次，只用來列出實際打包進去的套件（第三方授權聲明）
        Invoke-Native "前端 source map build" {
            npx vite build --sourcemap --outDir $maps --emptyOutDir --logLevel error }
    } finally { Pop-Location }
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
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # -UseBasicParsing：PowerShell 5.1 沒有它會去找 IE 引擎，沒裝 IE 的機器直接失敗
    Invoke-WebRequest $embedUrl -OutFile $embedCache -UseBasicParsing
}
$got = (Get-FileHash $embedCache -Algorithm SHA256).Hash.ToLower()
if ($got -ne $embedSha256) {
    Remove-Item $embedCache
    throw "$embedZip 的 SHA256 不符（$got），已刪掉快取，請重跑"
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

Write-Host "== 安裝相依套件（鎖定版本）=="
$site = Join-Path $runtime "Lib\site-packages"
# Microsoft Store 版 Python 的 pip 預設 --user，跟 --target 互斥，明確關掉
$env:PIP_USER = "false"
$requirements = Join-Path $root "scripts\package-requirements.txt"
Invoke-Native "pip install" {
    & $python -m pip install --target $site --no-deps --only-binary=:all: --no-warn-script-location `
        --disable-pip-version-check --quiet -r $requirements }

# ---- 4. C# 啟動器 ----
Write-Host "== 啟動器 publish =="
$proj = Join-Path $root "launcher\ResumeAutoFill.Launcher"
$publish = Join-Path $cache "launcher-publish"
Invoke-Native "dotnet publish" { dotnet publish $proj -c Release -o $publish --nologo -v q }
Copy-Item (Join-Path $cache "launcher-publish\ResumeAutoFill.exe") $dist

# ---- 5. llama-server 與資料夾骨架 ----
if (-not $Quick) {
    Write-Host "== 複製 llama-server（含 CUDA DLL，約 650 MB）=="
    $binOut = Join-Path $dist "bin"
    New-Item -ItemType Directory -Force $binOut | Out-Null
    foreach ($f in $binFiles) {
        $src = Join-Path $Bin $f
        if (-not (Test-Path $src)) { throw "$src 不存在" }
        Copy-Item $src $binOut
    }
    # CPU 後端依指令集分好幾個版本（haswell、zen4……），執行時挑適合這台機器的
    $cpu = Get-ChildItem $Bin -Filter "ggml-cpu-*.dll"
    if (-not $cpu) { throw "$Bin 裡沒有 ggml-cpu-*.dll" }
    $cpu | Copy-Item -Destination $binOut
}
foreach ($d in "models", "data") {
    New-Item -ItemType Directory -Force (Join-Path $dist $d) | Out-Null
}

# ---- 6. 授權與說明 ----
Copy-Item (Join-Path $root "LICENSE") (Join-Path $dist "LICENSE.txt")
if (-not $Quick) {
    $notices = Join-Path $dist "THIRD-PARTY-NOTICES.txt"
    Invoke-Native "第三方授權聲明" {
        & $python (Join-Path $root "tools\third_party_notices.py") $site $maps $notices }
}
$sizeGb = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
@"
Resume AutoFill v$version
======================
Word 履歷表自動填寫工具：在自己的電腦上執行，個人資料不會上傳到任何地方。

需求
- Windows 10 / 11（64 位元）
- 建議 NVIDIA 顯示卡、8 GB VRAM 以上，驅動程式要支援 CUDA 13：
  在「命令提示字元」執行 nvidia-smi，右上角的「CUDA Version」是 13.0 以上即可，
  不到就先到 NVIDIA 官網更新驅動程式。
  沒有獨立顯示卡也能跑（CPU），但一份表格的分析會從約一分鐘變成數十分鐘。
- 硬碟空間：程式本身約 $([math]::Round($sizeGb, 1)) GB，AI 模型另外約 6 GB

開始使用
1. 雙擊 ResumeAutoFill.exe，瀏覽器會自動開啟操作介面。
2. 首次使用：點右上角「模型未啟動」→ 在 Qwen3.5-9B 按「下載」（約 6 GB，只需一次）
   → 下載完按「切換」啟動，等 1～2 分鐘顯示「就緒」。
3. 之後每次開程式，會自動把上次用的模型載回來。
4. 舊版 .doc 請先用 Word 另存成 .docx 再上傳。本程式不需要安裝任何其他軟體。

資料與移除
- 個人資料只存在本資料夾的 data\ 裡，不會上傳。刪掉整個資料夾即完整移除。
- 除了第一次下載模型，使用時完全不需要網路。

結束程式：工作列右下角系統匣圖示 → 右鍵 → 結束。
完整說明與問題回報：https://github.com/Wade0325/resume-autofill
授權：本程式見 LICENSE.txt（MIT），隨附的第三方元件見 THIRD-PARTY-NOTICES.txt。
"@ | Out-File (Join-Path $dist "README.txt") -Encoding utf8

# ---- 7. zip ----
if ($Zip) {
    Write-Host "== 壓縮 =="
    $zipName = "Resume_AutoFill-v$version.zip"
    $zipPath = Join-Path $distRoot $zipName
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    # Windows 內建的 bsdtar：比 PowerShell 5.1 的 Compress-Archive 快得多，路徑也是標準的正斜線
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    Invoke-Native "壓縮" { & $tar -a -c -f $zipPath -C $distRoot "Resume_AutoFill" }
    $size = (Get-Item $zipPath).Length
    if ($size -ge 2GB) {
        throw "zip 有 $([math]::Round($size / 1GB, 2)) GB，超過 GitHub Release 單一檔案 2 GB 的上限"
    }
    $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
    [IO.File]::WriteAllText((Join-Path $distRoot "SHA256SUMS.txt"), "$hash  $zipName`n")
    Write-Host ("zip：{0}（{1:N0} MB）SHA256 {2}" -f $zipName, ($size / 1MB), $hash)
}

Write-Host "完成 → $dist"

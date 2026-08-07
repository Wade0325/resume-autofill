<#
    .\dev.ps1            後端＋前端，各開一個新視窗
    .\dev.ps1 backend    單一服務跑在目前視窗，Ctrl+C 結束
    .\dev.ps1 frontend
    .\dev.ps1 llm        平常不用，介面切換模型時後端會自己起
    .\dev.ps1 stop

    存檔必須是 UTF-8 with BOM，PowerShell 5.1 沒 BOM 會讀成亂碼。
#>
param([ValidateSet("all", "backend", "frontend", "llm", "stop")][string]$Service = "all")

$svc = [ordered]@{
    backend  = @{ Port = 8090; Dir = $PSScriptRoot;            Cmd = "python -m uvicorn backend.main:app --port 8090 --reload" }
    frontend = @{ Port = 5177; Dir = "$PSScriptRoot\frontend"; Cmd = "npm run dev" }
    llm      = @{ Port = 8085; Dir = $PSScriptRoot;            Cmd = ".\scripts\start-llama-server.ps1" }
}

function Get-Owner($Port) {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Ignore).OwningProcess | Select-Object -Unique
}

if ($Service -eq "stop") {
    $owners = @($svc.Values | ForEach-Object { Get-Owner $_.Port })
    if (-not $owners) { "沒有服務在跑"; return }
    # /T 連子行程一起殺：uvicorn --reload 是父子兩個行程，只殺父的會留下孤兒佔著埠
    $owners | ForEach-Object { taskkill /PID $_ /T /F }
    return
}

$all = $Service -eq "all"
foreach ($n in $(if ($all) { "backend", "frontend" } else { $Service })) {
    $s = $svc[$n]
    if (Get-Owner $s.Port) { throw "$n 的埠 $($s.Port) 已被佔用，先跑 .\dev.ps1 stop" }
    if ($all) { Start-Process powershell -WorkingDirectory $s.Dir -ArgumentList "-NoExit", "-Command", $s.Cmd }
    else      { Start-Process powershell -WorkingDirectory $s.Dir -ArgumentList "-Command", $s.Cmd -NoNewWindow -Wait }
}
if ($all) {
    "`n開發請開 http://localhost:5177"
    "8090 只拿來看 API（/docs）。用 8090 開介面會是上次 npm run build 的舊畫面，改了程式也不會變"
    "停止： .\dev.ps1 stop"
}

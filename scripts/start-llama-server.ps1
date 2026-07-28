<#
    開發用：啟動 llama-server（正式版由 ResumeAutoFill.exe 在背景執行）

    用法：  .\scripts\start-llama-server.ps1
            .\scripts\start-llama-server.ps1 -Model models\Qwen3.5-9B-Q4_K_M.gguf
#>
param(
    [string]$Model   = "models\Qwen3.5-4B-Q4_K_M.gguf",
    [int]   $Port    = 8085,
    [int]   $CtxSize = 16384,
    [int]   $NGpuLayers = 999          # 999 = 全部丟給 GPU；純 CPU 跑改成 0
)

$root = Split-Path $PSScriptRoot -Parent
$exe  = Join-Path $root "bin\llama-server.exe"
$gguf = Join-Path $root $Model

if (-not (Test-Path $exe))  { throw "找不到 $exe，請先取得 llama.cpp 二進位檔（見 README 第 5 節）" }
if (-not (Test-Path $gguf)) { throw "找不到模型 $gguf，請先下載 GGUF（見 README 第 5 節）" }

Write-Host "模型     : $Model"
Write-Host "端點     : http://localhost:$Port"
Write-Host "上下文   : $CtxSize"
Write-Host ""

# --ctx-size     : 16384 不是隨便訂的。真實履歷（77 個位置）光是欄位對映
#                  就要 7000 tokens 以上，8192 會在生成到一半時被截斷，
#                  症狀是 finish_reason=length、整份文件完全填不了
# --jinja        : 使用模型自帶的 chat template（Qwen3.5 需要）
# --temp 0       : 分類任務要可重現，不要隨機性
# --reasoning off: Qwen3.5 預設開 thinking，會先吐數千字推理才給答案，
#                  常在輸出 JSON 前就撞到長度上限。這裡是伺服器層的保險，
#                  backend/core/llm.py 送請求時也會帶 enable_thinking=false
& $exe -m $gguf --port $Port --ctx-size $CtxSize --n-gpu-layers $NGpuLayers `
       --jinja --temp 0 --reasoning off

param([int]$Port=8765)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$agentPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $agentPython)) {
    Write-Host '首次启动：创建本地环境并安装依赖。'
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建Python环境，请安装Python 3.11或更新版本。' }
    & $agentPython -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重新运行。' }
}
Write-Host "楼栋财务问数：http://127.0.0.1:$Port"
Write-Host '关闭此窗口或按Ctrl+C停止服务。API Key可通过config.local.json指定txt文件。'
& $agentPython -m uvicorn app:app --host 127.0.0.1 --port $Port

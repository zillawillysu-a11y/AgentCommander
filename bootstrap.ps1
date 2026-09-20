$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host 'AgentCommander 安裝檢查'
foreach ($name in @('git','python','codex','pi')) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { Write-Host "缺少 $name；請先安裝再重試。"; exit 1 }
}
python -c 'import mcp' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '安裝 AgentCommander Python 依賴...'
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
python -c 'from commander.task_store import state_root; state_root().mkdir(parents=True, exist_ok=True)' *> $null
if ($LASTEXITCODE -ne 0) { Write-Host '無法建立本機 runtime 目錄。'; exit 1 }
& "$PSScriptRoot\install_mcp.bat"
if ($LASTEXITCODE -ne 0) { exit 1 }
& "$PSScriptRoot\doctor.ps1"
exit $LASTEXITCODE

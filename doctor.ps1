$ErrorActionPreference = 'Continue'
Write-Host 'AgentCommander 環境檢查'
$failed = $false
function Check-Command($label, $name, $arguments) {
    $found = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $found) { Write-Host "$label`tFAIL（找不到 $name）"; $script:failed = $true; return }
    & $name @arguments *> $null
    if ($LASTEXITCODE -eq 0) { Write-Host "$label`tPASS" } else { Write-Host "$label`tFAIL"; $script:failed = $true }
}
Check-Command 'Git' 'git' @('--version')
Check-Command 'Python' 'python' @('--version')
Check-Command 'Codex CLI' 'codex' @('--version')
Check-Command 'Pi' 'pi' @('--version')
python -c 'import mcp' *> $null
if ($LASTEXITCODE -eq 0) { Write-Host "Python 依賴`tPASS" } else { Write-Host "Python 依賴`tFAIL（執行 python -m pip install -r requirements.txt）"; $failed = $true }
$models = @(pi --list-models 2>$null)
if ($models.Count -gt 1 -and ($models -join "`n") -match '(?i)qwen') { Write-Host "Local Model`tPASS" } else { Write-Host "Local Model`tFAIL（Pi 模型清單沒有 Qwen）"; $failed = $true }
$mcp = @(codex mcp list 2>$null)
if (($mcp -join "`n") -match 'agent-commander') { Write-Host "MCP`tPASS" } else { Write-Host "MCP`tFAIL（執行 .\install_mcp.bat）"; $failed = $true }
if (Get-Command orca -ErrorAction SilentlyContinue) { Write-Host "Orca`tPASS" } else { Write-Host "Orca`tOPTIONAL" }
if ($failed) { Write-Host 'RESULT: NOT READY'; exit 1 }
Write-Host 'RESULT: READY'

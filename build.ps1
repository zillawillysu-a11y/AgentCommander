$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
# Remove legacy one-file outputs so Codex cannot remain registered to a stale helper.
Remove-Item -LiteralPath 'dist\AgentCommander.exe' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'dist\AgentCommanderMCP.exe' -Force -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean AgentCommander.spec
$portable = Join-Path $root 'dist\AgentCommander'
$zip = Join-Path $root 'dist\AgentCommander-Portable.zip'
if (Test-Path $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path "$portable\*" -DestinationPath $zip
Write-Host "Portable build: $zip"

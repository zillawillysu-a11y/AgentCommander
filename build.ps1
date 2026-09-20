$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
python -m PyInstaller --noconfirm --clean AgentCommanderMCP.spec
python -m PyInstaller --noconfirm --clean AgentCommander.spec
$portable = Join-Path $root 'dist\AgentCommander'
New-Item -ItemType Directory -Force $portable | Out-Null
Copy-Item 'dist\AgentCommanderMCP.exe' $portable -Force
Copy-Item 'dist\AgentCommander.exe' $portable -Force
$zip = Join-Path $root 'dist\AgentCommander-Portable.zip'
if (Test-Path $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path "$portable\*" -DestinationPath $zip
Write-Host "Portable build: $zip"

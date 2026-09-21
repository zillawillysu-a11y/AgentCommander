$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$stageRoot = Join-Path $root 'dist-release'
$workRoot = Join-Path $root 'build-release'
$targetRoot = Join-Path $root 'dist\AgentCommander'
Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean --distpath $stageRoot --workpath $workRoot AgentCommander.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
$portable = Join-Path $stageRoot 'AgentCommander'
$helper = Join-Path $portable 'AgentCommanderMCP.exe'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Packaged helper missing: $helper" }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Source test suite failed with exit code $LASTEXITCODE" }

$previousHelper = $env:AGENTCOMMANDER_PACKAGED_HELPER
try {
    $env:AGENTCOMMANDER_PACKAGED_HELPER = $helper
    python -m pytest -q tests\test_process_lifecycle.py -k packaged_helper
    if ($LASTEXITCODE -ne 0) { throw "Packaged helper integration tests failed with exit code $LASTEXITCODE" }
}
finally {
    if ($null -eq $previousHelper) { Remove-Item Env:\AGENTCOMMANDER_PACKAGED_HELPER -ErrorAction SilentlyContinue }
    else { $env:AGENTCOMMANDER_PACKAGED_HELPER = $previousHelper }
}

$distRoot = Join-Path $root 'dist'
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
$zip = Join-Path $distRoot 'AgentCommander-Portable.zip'
if (Test-Path $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path "$portable\*" -DestinationPath $zip
$checksum = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumFile = "$zip.sha256"
Set-Content -LiteralPath $checksumFile -Value "$checksum  AgentCommander-Portable.zip" -Encoding ascii

# Deployment is deliberately last so a running Codex MCP cannot lock the long build.
$targetPrefix = [System.IO.Path]::GetFullPath($targetRoot) + [System.IO.Path]::DirectorySeparatorChar
Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($targetPrefix, [System.StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
Remove-Item -LiteralPath $targetRoot -Recurse -Force -ErrorAction SilentlyContinue
Move-Item -LiteralPath $portable -Destination $targetRoot
Remove-Item -LiteralPath $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Portable build: $zip"
Write-Host "SHA-256: $checksumFile"

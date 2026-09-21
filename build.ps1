$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
# Remove legacy one-file outputs so Codex cannot remain registered to a stale helper.
Remove-Item -LiteralPath 'dist\AgentCommander.exe' -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'dist\AgentCommanderMCP.exe' -Force -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean AgentCommander.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
$portable = Join-Path $root 'dist\AgentCommander'
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

$zip = Join-Path $root 'dist\AgentCommander-Portable.zip'
if (Test-Path $zip) { Remove-Item -LiteralPath $zip }
Compress-Archive -Path "$portable\*" -DestinationPath $zip
$checksum = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumFile = "$zip.sha256"
Set-Content -LiteralPath $checksumFile -Value "$checksum  AgentCommander-Portable.zip" -Encoding ascii
Write-Host "Portable build: $zip"
Write-Host "SHA-256: $checksumFile"

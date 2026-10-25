param([string]$Installer, [string]$HermesRoot)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$sourceDir = Join-Path $HermesRoot 'hermes-agent'
. $Installer -HermesHome $HermesRoot -InstallDir $sourceDir -NonInteractive -SkipBrowser -SkipComputerUse
Initialize-ResolvedPaths
$bootstrapPython = Get-BootstrapPython
Push-Location -LiteralPath $sourceDir
try {
    & $bootstrapPython -m pm.cli install python uv ripgrep --extra mcp
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

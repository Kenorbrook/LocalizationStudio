param([string]$Installer, [string]$HermesRoot, [string]$Runner, [string]$RequestFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$sourceDir = Join-Path $HermesRoot 'hermes-agent'
. $Installer -HermesHome $HermesRoot -InstallDir $sourceDir -NonInteractive -SkipBrowser -SkipComputerUse
Initialize-ResolvedPaths
$bootstrapPython = Get-BootstrapPython
$managedPython = & $bootstrapPython -I -X utf8 -c "import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from hermes_cli._launchers import resolve_store_python;print(resolve_store_python(Path(sys.argv[1])) or '')" $sourceDir
if ($LASTEXITCODE -ne 0 -or -not $managedPython) { throw 'Hermes runtime is not prepared; reinstall the plugin' }
Push-Location -LiteralPath $sourceDir
try {
    & $managedPython.Trim() -I -X utf8 $Runner $RequestFile $sourceDir
    exit $LASTEXITCODE
} finally {
    Pop-Location
}

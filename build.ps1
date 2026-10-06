$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m PyInstaller --noconfirm --distpath (Join-Path $PSScriptRoot 'release') --workpath (Join-Path $PSScriptRoot 'build') (Join-Path $PSScriptRoot 'packaging/LocalizationStudio.spec')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m PyInstaller --noconfirm --distpath (Join-Path $PSScriptRoot 'release') --workpath (Join-Path $PSScriptRoot 'build/updater') (Join-Path $PSScriptRoot 'packaging/LocalizationUpdater.spec')
if ($LASTEXITCODE -ne 0) { throw 'Updater build failed' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'release/LocalizationUpdater.exe') -Destination (Join-Path $PSScriptRoot 'release/LocalizationStudio/LocalizationUpdater.exe') -Force
Write-Host 'Built release/LocalizationStudio/LocalizationStudio.exe'

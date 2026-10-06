$ErrorActionPreference='Stop'
$env:PYTHONIOENCODING='utf-8'
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -m PyInstaller --noconfirm --distpath (Join-Path $PSScriptRoot 'release') --workpath (Join-Path $PSScriptRoot 'build') (Join-Path $PSScriptRoot 'packaging/LocalizationStudio.spec')
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
Write-Host 'Built release/LocalizationStudio/LocalizationStudio.exe'

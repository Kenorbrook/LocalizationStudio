param(
    [Parameter(Mandatory=$true)][string]$GameRoot,
    [ValidateSet('translate','review','pipeline','qa')][string]$Mode = 'pipeline',
    [string]$Model = 'qwen3.8:latest',
    [int]$Limit = 0,
    [ValidateRange(1,16)][int]$ChunkItems = 4,
    [ValidateRange(200,10000)][int]$ChunkChars = 2800,
    [ValidateRange(0,20)][int]$ContextLines = 3,
    [switch]$VisibleWindowChild,
    [int]$StudioJob = 0,
    [string]$StudioDb = '',
    [string]$StudioWorkerExecutable = '',
    [string]$StudioPython = '',
    [string]$StudioApp = '',
    [string]$PythonExecutable = ''
)
$ErrorActionPreference = 'Stop'
function Resolve-TranslationPython {
    if ($PythonExecutable) {
        if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw 'PythonExecutable does not exist' }
        return $PythonExecutable
    }
    $LocalPython = Join-Path $PSScriptRoot '../../.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $LocalPython -PathType Leaf) { return (Resolve-Path -LiteralPath $LocalPython).Path }
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($SystemPython) { return $SystemPython.Source }
    throw 'Install Python or pass -PythonExecutable <path>'
}

$ResolvedGame = (Resolve-Path -LiteralPath $GameRoot).Path
if ($StudioJob -gt 0) {
    if (-not $VisibleWindowChild) {
        $StudioArguments = @('-NoExit','-NoProfile','-File',('"{0}"' -f $PSCommandPath),'-GameRoot',('"{0}"' -f $ResolvedGame),'-StudioJob',"$StudioJob",'-StudioDb',('"{0}"' -f $StudioDb),'-VisibleWindowChild')
        if ($StudioWorkerExecutable) { $StudioArguments += @('-StudioWorkerExecutable',('"{0}"' -f $StudioWorkerExecutable)) }
        if ($StudioPython) { $StudioArguments += @('-StudioPython',('"{0}"' -f $StudioPython),'-StudioApp',('"{0}"' -f $StudioApp)) }
        $StudioShell = New-Object -ComObject Shell.Application
        $StudioShellExe = if (Test-Path (Join-Path $PSHOME 'pwsh.exe')) { Join-Path $PSHOME 'pwsh.exe' } else { Join-Path $PSHOME 'powershell.exe' }
        $StudioShell.ShellExecute($StudioShellExe,($StudioArguments -join ' '),$ResolvedGame,'open',1)
        return
    }
    $Host.UI.RawUI.WindowTitle = "Localization Studio - $(Split-Path $ResolvedGame -Leaf) - job $StudioJob"
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $env:PYTHONIOENCODING = 'utf-8'
    if ($StudioWorkerExecutable) {
        & $StudioWorkerExecutable --db $StudioDb --worker $StudioJob
    } elseif ($StudioPython -and $StudioApp) {
        & $StudioPython -u $StudioApp --db $StudioDb --worker $StudioJob
    } else {
        $StudioRuntime = Resolve-TranslationPython
        & $StudioRuntime -u (Join-Path $PSScriptRoot '../app.py') --db $StudioDb --worker $StudioJob
    }
    Write-Host 'This window remains open. Progress is saved in Localization Studio.'
    return
}
$TargetDirectory = Join-Path $ResolvedGame 'game/tl/russian'
if (-not (Test-Path -LiteralPath $TargetDirectory -PathType Container)) { throw "Missing translation layer: $TargetDirectory" }
$Runtime = Resolve-TranslationPython
if (-not (Test-Path -LiteralPath $Runtime -PathType Leaf)) { throw "Python runtime not found: $Runtime" }

if (-not $VisibleWindowChild) {
    $ChildArguments = @(
        '-NoExit', '-NoProfile', '-File', ('"{0}"' -f $PSCommandPath),
        '-GameRoot', ('"{0}"' -f $ResolvedGame),
        '-Mode', $Mode, '-Model', $Model,
        '-Limit', "$Limit", '-ChunkItems', "$ChunkItems",
        '-ChunkChars', "$ChunkChars", '-ContextLines', "$ContextLines",
        '-VisibleWindowChild'
    )
    $WindowsShell = New-Object -ComObject Shell.Application
    if ($PythonExecutable) { $ChildArguments += @('-PythonExecutable', ('"{0}"' -f $PythonExecutable)) }
    $ShellExe = if (Test-Path (Join-Path $PSHOME 'pwsh.exe')) { Join-Path $PSHOME 'pwsh.exe' } else { Join-Path $PSHOME 'powershell.exe' }
    $WindowsShell.ShellExecute($ShellExe, ($ChildArguments -join ' '), $ResolvedGame, 'open', 1)
    Write-Host 'Opened translation PowerShell through the Windows shell; progress and logs will remain visible.'
    exit 0
}

$Host.UI.RawUI.WindowTitle = "Translation - $(Split-Path $ResolvedGame -Leaf) - $Mode"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Test-OllamaReady {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 -NoProxy | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Wait-OllamaReady {
if (-not (Test-OllamaReady)) {
    Write-Host 'Waiting for local Ollama. VPN is not required.' -ForegroundColor Yellow
    for ($Attempt = 1; $Attempt -le 120; $Attempt++) {
        if ($Attempt -eq 10) {
            Write-Host 'Starting/checking Ollama...' -ForegroundColor Yellow
            & ollama ps
        }
        if (Test-OllamaReady) { break }
        Start-Sleep -Seconds 1
    }
    if (-not (Test-OllamaReady)) { throw 'Local Ollama did not become ready within 120 seconds.' }
}
}

if ($Mode -ne 'qa') { Wait-OllamaReady }

$ProjectTools = Join-Path $ResolvedGame 'translation_tools'
New-Item -ItemType Directory -Path $ProjectTools -Force | Out-Null
$LockPath = Join-Path $ProjectTools '.local-translation.lock'
$LockStream = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    if ($Mode -ne 'qa') {
        $BackupDirectory = Join-Path $ProjectTools "backups/shared-$Stamp"
        New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
        Copy-Item -LiteralPath $TargetDirectory -Destination (Join-Path $BackupDirectory 'russian') -Recurse
        Get-ChildItem -LiteralPath $ProjectTools -File -Force | Where-Object { $_.Name -match 'cache.*\.json$|^localization_config\.json$' } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $BackupDirectory
        }
        Write-Host "Backup: $BackupDirectory"
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $RunArguments = @((Join-Path $PSScriptRoot 'localize.py'), $Mode, '--root', $ResolvedGame, '--model', $Model,
        '--chunk-items', "$ChunkItems", '--chunk-chars', "$ChunkChars", '--context-lines', "$ContextLines")
    if ($Limit -gt 0) { $RunArguments += @('--limit', "$Limit") }
    $Log = Join-Path $ProjectTools "shared-$Mode-$Stamp.log"
    $RecoveryAttempt = 0
    while ($true) {
        & $Runtime -u @RunArguments 2>&1 | Tee-Object -FilePath $Log -Append
        $RunExit = $LASTEXITCODE
        if ($RunExit -eq 0) { break }
        $RecentLog = Get-Content -LiteralPath $Log -Tail 80 -Raw
        if ($RecentLog -notmatch 'Ollama connection failed|WinError 10061|ConnectionRefusedError') { break }
        $RecoveryAttempt++
        if ($RecoveryAttempt -gt 20) { break }
        Write-Host "Ollama disconnected. Restoring local service and resuming from cache ($RecoveryAttempt/20)..." -ForegroundColor Yellow
        Wait-OllamaReady
    }
}
finally {
    $LockStream.Dispose()
    Remove-Item -LiteralPath $LockPath
}
if ($VisibleWindowChild) {
    $global:LASTEXITCODE = $RunExit
    Write-Host "Translation process finished with exit code $RunExit. This window remains open."
    return
}
exit $RunExit

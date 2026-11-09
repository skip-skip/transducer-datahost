# run_ingest.ps1
#
# Task Scheduler wrapper for the Phase 3 loader. It prevents overlapping runs
# with a machine-wide mutex and appends timestamped output to a daily log.
#
# Example:
#   .\scripts\run_ingest.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$Python = "python",

    [string]$LogDirectory = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$ingestDir = Join-Path $repoRoot "ingest"

if (-not $LogDirectory) {
    $LogDirectory = Join-Path $repoRoot "logs"
}
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$logFile = Join-Path $LogDirectory ("ingest-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $logFile -Value $line
    Write-Host $line
}

$mutex = New-Object System.Threading.Mutex($false, "Local\TransducerIngest")
$acquired = $false
try {
    $acquired = $mutex.WaitOne(0)
}
catch [System.Threading.AbandonedMutexException] {
    # The previous process crashed while holding the mutex; take ownership.
    $acquired = $true
}

if (-not $acquired) {
    Write-Log "Another ingest run is already in progress; exiting."
    $mutex.Dispose()
    exit 0
}

$exitCode = 1
try {
    Write-Log "Starting ingest from $Source"
    Push-Location $ingestDir
    try {
        $env:PYTHONPATH = $ingestDir
        & $Python -m transducer_ingest.cli ingest --source $Source 2>&1 |
            ForEach-Object { Write-Log $_ }
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Log "Ingest failed: $_"
    $exitCode = 1
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}

Write-Log "Ingest finished with exit code $exitCode"
exit $exitCode

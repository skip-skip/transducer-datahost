# upload_xle.ps1
#
# Manual uploader for the VM-hosted ingest gateway. It prevents overlapping
# runs with a machine-wide mutex, appends timestamped output to a daily log,
# and delegates scanning/hashing/retries to `transducer-ingest upload`.
#
# The uploader talks HTTPS with a bearer token. It never receives a database
# credential.
#
# Examples:
#   .\scripts\upload_xle.ps1 -Source "C:\path\to\OneDrive_1_11-9-2026"
#   .\scripts\upload_xle.ps1 -Source "C:\path\to\export" -NoWait
#   .\scripts\upload_xle.ps1 -Source "C:\path\to\export" -Ca "C:\certs\rootCA.pem"
#
# Environment (or parameters):
#   INGEST_API_URL        default https://transducer.internal:8443/ingest
#   INGEST_API_TOKEN      bearer token; prompted when absent on a terminal
#   INGEST_CA_CERT        optional CA bundle for the internal certificate
#   INGEST_SETTLE_SECONDS default 120

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [string]$Api = "",

    [string]$Token = "",

    [string]$Ca = "",

    # -1 keeps the environment/default (120 seconds).
    [int]$SettleSeconds = -1,

    [switch]$NoWait,

    [string]$ClientName = "",

    [string]$Python = "python",

    [string]$LogDirectory = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$ingestDir = Join-Path $repoRoot "ingest"

if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
    Write-Error "source folder not found: $Source"
    exit 2
}

if (-not $LogDirectory) {
    $LogDirectory = Join-Path $repoRoot "logs"
}
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$logFile = Join-Path $LogDirectory ("upload-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line
}

$mutex = New-Object System.Threading.Mutex($false, "Global\TransducerUpload")
if (-not $mutex.WaitOne(0)) {
    Write-Log "another upload is already running; exiting"
    Write-Error "another upload is already running"
    $mutex.Dispose()
    exit 3
}

try {
    Write-Log "upload start source='$Source' api='$Api' no_wait=$($NoWait.IsPresent)"

    if ($Api) {
        $env:INGEST_API_URL = $Api
    }
    if ($Token) {
        $env:INGEST_API_TOKEN = $Token
    }
    if ($SettleSeconds -ge 0) {
        $env:INGEST_SETTLE_SECONDS = [string]$SettleSeconds
    }

    $arguments = @("-m", "transducer_ingest.cli", "upload", "--source", $Source)
    if ($Ca) {
        $arguments += @("--ca", $Ca)
    }
    if ($NoWait) {
        $arguments += "--no-wait"
    }
    if ($ClientName) {
        $arguments += @("--client-name", $ClientName)
    }

    Push-Location $ingestDir
    try {
        & $Python @arguments 2>&1 | ForEach-Object {
            $text = [string]$_
            Write-Host $text
            Write-Log $text
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    Write-Log "upload end exit=$exitCode"
    exit $exitCode
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}

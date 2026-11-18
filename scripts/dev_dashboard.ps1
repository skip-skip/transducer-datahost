<#
.SYNOPSIS
    Run the transducer dashboard locally with fake data.

.DESCRIPTION
    Two loops:

      fast  - Panel only (panel serve --dev). No login, CSP, or proxy.
      prod  - FastAPI + Panel with the real auth/CSP stack (uvicorn --reload).

    State is kept under %TEMP%\transducer-dashboard-dev and never touches the
    production volume. Fake data is the default; -RealApi uses READ_API_URL.

.EXAMPLE
    .\scripts\dev_dashboard.ps1 -ResetState

.EXAMPLE
    .\scripts\dev_dashboard.ps1 -Mode prod

.EXAMPLE
    .\scripts\dev_dashboard.ps1 -Mode prod -RealApi -CaBundle C:\certs\server.crt
#>
[CmdletBinding()]
param(
    [ValidateSet("fast", "prod")]
    [string]$Mode = "fast",

    [int]$Port = 0,

    [string]$RootPath = "",

    [switch]$ResetState,
    [switch]$NoSeed,
    [switch]$RealApi,

    [string]$Api = "",
    [string]$CaBundle = ""
)

$ErrorActionPreference = "Stop"

if ($RealApi -and $Mode -ne "prod") {
    throw "-RealApi requires -Mode prod."
}
if ($Api -and -not $RealApi) {
    throw "-Api requires -RealApi."
}
if ($CaBundle -and -not $RealApi) {
    throw "-CaBundle requires -RealApi."
}
if ($RootPath -and $Mode -ne "prod") {
    throw "-RootPath requires -Mode prod; the fast Panel loop has no proxy."
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

if ($Port -le 0) {
    $Port = if ($Mode -eq "fast") { 8081 } else { 8080 }
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "-Port must be between 1 and 65535."
}

$Prefix = ""
if ($RootPath) {
    $Prefix = "/" + $RootPath.Trim("/")
}
$Url = "http://127.0.0.1:$Port$Prefix/"

$StateDir = Join-Path ([System.IO.Path]::GetTempPath()) "transducer-dashboard-dev"

if ($ResetState) {
    $fullState = [System.IO.Path]::GetFullPath($StateDir)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if (
        -not $fullState.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $fullState -notlike "*transducer-dashboard-dev*"
    ) {
        throw "Refusing to reset unexpected state directory: $fullState"
    }
    if (Test-Path -LiteralPath $fullState) {
        Remove-Item -LiteralPath $fullState -Recurse -Force
        Write-Host "Removed $fullState"
    }
}

if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $owner = ($listener | Select-Object -First 1).OwningProcess
        $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
        throw "Port $Port is already in use by PID $owner ($($process.ProcessName)). Stop it or pass -Port."
    }
}

$touched = @(
    "DASHBOARD_DEV_STATE_DIR",
    "DASHBOARD_STATE_DIR",
    "DASHBOARD_SECRET_KEY",
    "DASHBOARD_COOKIE_SECURE",
    "DASHBOARD_DEV_REAL_API",
    "DASHBOARD_DEV_SEED",
    "DASHBOARD_DEV_ROOT_PATH",
    "READ_API_URL",
    "SSL_CERT_FILE"
)
$saved = @{}
foreach ($name in $touched) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

$exitCode = 0
try {
    if ($RealApi) {
        if ($Api) {
            $env:READ_API_URL = $Api
        }
        if (-not $env:READ_API_URL) {
            throw "-RealApi needs READ_API_URL or -Api."
        }
        if ($CaBundle) {
            $env:SSL_CERT_FILE = (Resolve-Path -LiteralPath $CaBundle).Path
        }
    }

    $env:DASHBOARD_DEV_STATE_DIR = $StateDir
    $env:DASHBOARD_STATE_DIR = $StateDir
    $env:DASHBOARD_SECRET_KEY = "dev-only-secret-key-1234567890"
    $env:DASHBOARD_COOKIE_SECURE = "false"
    $env:DASHBOARD_DEV_REAL_API = if ($RealApi) { "1" } else { "0" }
    $env:DASHBOARD_DEV_SEED = if ($NoSeed) { "0" } else { "1" }
    $env:DASHBOARD_DEV_ROOT_PATH = $Prefix

    Write-Host "Transducer dashboard development server"
    Write-Host "  mode:  $Mode"
    Write-Host "  url:   $Url"
    Write-Host "  state: $StateDir"
    if ($RealApi) {
        Write-Host "  data:  $env:READ_API_URL (real read API)"
        Write-Host "  login: use a read API token"
    } elseif ($Mode -eq "prod") {
        Write-Host "  data:  fake fixtures"
        Write-Host '  login: token "dev"'
    } else {
        Write-Host "  data:  fake fixtures (no login in fast mode)"
    }
    Write-Host ""

    if ($Mode -eq "fast") {
        $arguments = @(
            "-m", "panel", "serve",
            (Join-Path $RepoRoot "dashboard\dev_panel.py"),
            "--dev",
            "--index", "dev_panel",
            "--address", "127.0.0.1",
            "--port", "$Port"
        )
        & $Python @arguments
    } else {
        $arguments = @(
            "-m", "uvicorn", "dev_fastapi:app",
            "--app-dir", (Join-Path $RepoRoot "dashboard"),
            "--reload",
            "--reload-dir", (Join-Path $RepoRoot "dashboard"),
            "--host", "127.0.0.1",
            "--port", "$Port"
        )
        & $Python @arguments
    }

    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
} finally {
    foreach ($name in $touched) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
    }
}

exit $exitCode

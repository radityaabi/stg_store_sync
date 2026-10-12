<#
============================================================================
 STG Store Sync — One-command environment setup (Windows / VDI)
============================================================================
 Prepares this repo to run, and to run headlessly on its per-job schedule:
   1. venv      — create .venv if missing (reuse it if present)
   2. deps      — pip install -e ".[dev]" into the venv
   3. .env      — copy .env.example and fill STG_* if missing (never overwrite)
   4. verify    — run the test suite in the venv (gates step 5)
   5. schedule  — register one OS task per job (schtasks) via `schedule sync`

 Idempotent: safe to re-run. An existing .env is never modified.

 Usage (in PowerShell):
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\scripts\setup.ps1
   .\scripts\setup.ps1 -StgServer https://host/api -StgUser u -StgPassword p
   .\scripts\setup.ps1 -SkipTests -SkipSchedule

 Prerequisites: PowerShell 5.1+, Python 3.12+, schtasks (built into Windows)
============================================================================
#>
[CmdletBinding()]
param(
    [string]$StgServer = "",
    [string]$StgUser = "",
    [string]$StgPassword = "",
    [switch]$NonInteractive,
    [switch]$SkipTests,
    [switch]$SkipSchedule,
    [switch]$Help
)

if ($Help) {
    Write-Host @"
Usage: .\scripts\setup.ps1 [options]

Options:
  -StgServer URL       STG REST base URL (enables non-interactive)
  -StgUser USER        STG Studio username
  -StgPassword PASS    STG Studio password
  -NonInteractive      Never prompt; use the values above
  -SkipTests           Do not run the test suite (step 4)
  -SkipSchedule        Do not register OS tasks (step 5)
  -Help                Show this help
"@
    exit 0
}

$ErrorActionPreference = "Stop"

# -- Colors ------------------------------------------------------------------
function Info($m) { Write-Host "i  $m" -ForegroundColor Blue }
function Ok($m)   { Write-Host "v  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!  $m" -ForegroundColor Yellow }
function Err($m)  { Write-Host "x  $m" -ForegroundColor Red }
function Step($m) { Write-Host ""; Write-Host "-- $m --" -ForegroundColor Cyan }

# -- Locate the project root from this script's own path (never CWD) ---------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir
Info "Project dir: $ProjectDir"

# -- 1. Find Python 3.12+ ----------------------------------------------------
Step "1/5  Python"
$python = $null
$pythonArgs = @()
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.13", "3.12")) { $candidates += , @("py", @("-$v")) }
}
foreach ($cmd in @("python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $candidates += , @($cmd, @()) }
}
foreach ($c in $candidates) {
    try {
        $out = & $c[0] @($c[1]) --version 2>&1
        if ("$out" -match "Python (\d+)\.(\d+)") {
            if ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 12) {
                $python = $c[0]; $pythonArgs = $c[1]; break
            }
        }
    } catch { }
}
if (-not $python) {
    Err "Python 3.12+ not found on PATH (tried py -3.13/-3.12, python, python3)."
    exit 1
}
Ok "Using $python $($pythonArgs -join ' ') ($(& $python @pythonArgs --version 2>&1))"

# -- 2. venv ----------------------------------------------------------------
Step "2/5  Virtual environment"
$venvPy = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Ok ".venv already exists — reusing"
} else {
    & $python @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Err "venv creation failed"; exit 1 }
    Ok "Created .venv"
}

# -- 3. Dependencies ---------------------------------------------------------
Step "3/5  Dependencies"
& $venvPy -m pip install --upgrade pip | Out-Null
& $venvPy -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { Err "pip install failed"; exit 1 }
Ok "Installed the package + dev extras"

# -- 4. .env ----------------------------------------------------------------
Step "4/5  Credentials (.env)"
if (Test-Path ".env") {
    Ok ".env exists — leaving it untouched"
} else {
    Copy-Item ".env.example" ".env"
    Info "Created .env from .env.example"
    if (-not $NonInteractive) {
        $StgServer   = Read-Host "STG_SERVER (e.g. https://host/api)"
        $StgUser     = Read-Host "STG_USER"
        $sec         = Read-Host "STG_PASSWORD" -AsSecureString
        $StgPassword = [System.Net.NetworkCredential]::new("", $sec).Password
    }
    if ($StgServer -and $StgUser -and $StgPassword) {
        $text = Get-Content ".env" -Raw
        $text = $text -replace '(?m)^STG_SERVER=.*$',   "STG_SERVER=$StgServer"
        $text = $text -replace '(?m)^STG_USER=.*$',     "STG_USER=$StgUser"
        $text = $text -replace '(?m)^STG_PASSWORD=.*$', "STG_PASSWORD=$StgPassword"
        Set-Content ".env" $text -NoNewline
        Ok "Wrote credentials into .env"
    } else {
        Warn ".env created but STG_* left blank — edit it before a live run"
    }
}

# -- 5. Verify (gates step 6) -----------------------------------------------
Step "5/5  Verify"
if ($SkipTests) {
    Warn "Tests skipped (-SkipTests); OS tasks will NOT be registered"
    $SkipSchedule = $true
} else {
    & $venvPy -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Err "Test suite failed — not registering OS tasks"
        exit 1
    }
    Ok "Test suite passed"
}

# -- 6. OS schedule (best-effort) -------------------------------------------
Step "6  OS schedule"
if ($SkipSchedule) {
    Info "Skipped. Register later with: .\.venv\Scripts\python.exe -m store_sync.cli schedule sync"
} else {
    & $venvPy -m store_sync.cli schedule sync
    if ($LASTEXITCODE -eq 0) {
        Ok "OS tasks reconciled"
    } else {
        Warn "Could not register OS tasks (schtasks refused?)."
        Info "Register later with: .\.venv\Scripts\python.exe -m store_sync.cli schedule sync"
    }
}

Write-Host ""
Ok "Setup complete."
Info "Run the GUI:      .\.venv\Scripts\pythonw.exe -m store_sync.gui"
Info "Run headlessly:   .\.venv\Scripts\python.exe -m store_sync.cli list"

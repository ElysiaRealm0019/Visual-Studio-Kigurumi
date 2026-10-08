<#
.SYNOPSIS
  Start Visual Studio Kigurumi (V.S.K) locally without Docker: FastAPI backend + Vite frontend.

.DESCRIPTION
  Postgres, Redis, MinIO and the worker container are not needed for local runs:
  generation jobs run inside the API process and are stored in SQLite and runtime/.
  The backend reads .env from the repo root. Press Ctrl+C to stop both processes.

.PARAMETER ApiPort
  Backend port. Default 18000 (the frontend dev proxy points here).

.PARAMETER FrontendPort
  Frontend dev server port. Default 5173.

.PARAMETER Lan
  Expose the frontend on the local network (for opening it on a phone). Default: localhost only.

.PARAMETER SkipInstall
  Skip the dependency check / installation step.
#>
param(
  [int]$ApiPort = 18000,
  [int]$FrontendPort = 5173,
  [switch]$Lan,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$logDir = Join-Path $root "runtime\local-logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path (Join-Path $root ".env"))) {
  Write-Warning ".env not found. Copy .env.example to .env and set LLM_PROVIDER / IMAGE_PROVIDER first."
}

# --- Backend dependencies -------------------------------------------------
if (-not (Test-Path $venvPython)) {
  if ($SkipInstall) { throw "backend\.venv is missing. Run without -SkipInstall to create it." }
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue).Source }
  if (-not $python) { throw "Python 3.12+ was not found on PATH." }
  Write-Host "Creating backend\.venv ..." -ForegroundColor Cyan
  & $python -m venv (Join-Path $backendDir ".venv")
  & $venvPython -m pip install -e "$backendDir[dev]"
  if ($LASTEXITCODE -ne 0) { throw "Backend dependency install failed." }
}

# --- Frontend dependencies ------------------------------------------------
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm was not found. Install Node.js 22+." }
if (-not $SkipInstall -and -not (Test-Path (Join-Path $frontendDir "node_modules"))) {
  Write-Host "Installing frontend dependencies ..." -ForegroundColor Cyan
  Push-Location $frontendDir
  try {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
  } finally {
    Pop-Location
  }
}

# --- Codex CLI path -------------------------------------------------------
# The official installer puts codex.exe under LOCALAPPDATA without adding it to every shell's PATH.
# Environment variables override .env, so only set CODEX_PATH when it is not already resolvable.
if (-not $env:CODEX_PATH -and -not (Get-Command codex -ErrorAction SilentlyContinue)) {
  $codexExe = Join-Path $env:LOCALAPPDATA "Programs\OpenAI\Codex\bin\codex.exe"
  if (Test-Path $codexExe) {
    $env:CODEX_PATH = $codexExe
    Write-Host "Using Codex CLI at $codexExe" -ForegroundColor DarkGray
  }
}

# --- Proxy ----------------------------------------------------------------
# Codex CLI and httpx only read HTTP(S)_PROXY, not the Windows system proxy. Without it Codex
# retries its WebSocket connection for ~2 minutes per call before falling back to HTTPS.
if (-not $env:HTTPS_PROXY -and -not $env:HTTP_PROXY) {
  $internetSettings = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings" -ErrorAction SilentlyContinue
  if ($internetSettings -and $internetSettings.ProxyEnable -eq 1 -and $internetSettings.ProxyServer) {
    $proxyServer = [string]$internetSettings.ProxyServer
    # Per-protocol form: "http=host:port;https=host:port;..."
    if ($proxyServer -match "(?:^|;)https=([^;]+)") {
      $proxyServer = $Matches[1]
    } elseif ($proxyServer -match "(?:^|;)http=([^;]+)") {
      $proxyServer = $Matches[1]
    }
    if ($proxyServer -notmatch "^[a-z]+://") { $proxyServer = "http://$proxyServer" }
    $env:HTTP_PROXY = $proxyServer
    $env:HTTPS_PROXY = $proxyServer
    if (-not $env:NO_PROXY) { $env:NO_PROXY = "localhost,127.0.0.1,::1,.local" }
    Write-Host "Using system proxy $proxyServer" -ForegroundColor DarkGray
  }
}

# --- Start backend --------------------------------------------------------
$backendOut = Join-Path $logDir "backend.out.log"
$backendErr = Join-Path $logDir "backend.err.log"
$backend = Start-Process -FilePath $venvPython `
  -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "$ApiPort") `
  -WorkingDirectory $root `
  -RedirectStandardOutput $backendOut `
  -RedirectStandardError $backendErr `
  -NoNewWindow -PassThru

try {
  $healthy = $false
  for ($i = 0; $i -lt 30; $i++) {
    if ($backend.HasExited) { break }
    try {
      Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2 | Out-Null
      $healthy = $true
      break
    } catch {
      Start-Sleep -Seconds 1
    }
  }
  if (-not $healthy) {
    Get-Content $backendErr -Tail 30 -ErrorAction SilentlyContinue
    throw "Backend did not become healthy. See $backendErr"
  }
  Write-Host "Backend:  http://127.0.0.1:$ApiPort  (logs: runtime\local-logs)" -ForegroundColor Green

  # --- Start frontend (foreground) ---------------------------------------
  $frontendHost = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
  $env:VITE_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"
  Write-Host "Frontend: http://localhost:$FrontendPort  (Ctrl+C stops both)" -ForegroundColor Green
  Push-Location $frontendDir
  try {
    npm run dev -- --host $frontendHost --port $FrontendPort --strictPort
  } finally {
    Pop-Location
  }
} finally {
  if (-not $backend.HasExited) {
    Write-Host "Stopping backend ..." -ForegroundColor DarkGray
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
  }
}

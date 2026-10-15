$ErrorActionPreference = "Stop"

$appTarget = "legaldoc_extraction.api:app"
$frontendHost = if ($env:FRONTEND_HOST) { $env:FRONTEND_HOST } else { "localhost" }
$frontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5173" }
$backendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } elseif ($env:HOST) { $env:HOST } else { "localhost" }
$backendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } elseif ($env:PORT) { $env:PORT } else { "8000" }
$runBackend = $true
if ($env:RUN_BACKEND -eq "0") {
    $runBackend = $false
}

if (-not (Test-Path "frontend/node_modules")) {
    Push-Location frontend
    try {
        pnpm install
    }
    finally {
        Pop-Location
    }
}

$backendProcess = $null

try {
    if ($runBackend) {
        uv sync
        $env:HOST = $backendHost
        $env:PORT = $backendPort
        $env:FRONTEND_HOST = $frontendHost
        $env:FRONTEND_PORT = $frontendPort
        $env:VITE_BACKEND_URL = "http://${backendHost}:$backendPort"
        $backendProcess = Start-Process `
            -FilePath "uv" `
            -ArgumentList @("run", "uvicorn", $appTarget, "--reload", "--host", $backendHost, "--port", $backendPort) `
            -WorkingDirectory $PSScriptRoot `
            -PassThru
        Write-Host "Backend API running on $($env:VITE_BACKEND_URL)"
    }
    else {
        Remove-Item Env:VITE_BACKEND_URL -ErrorAction SilentlyContinue
        Write-Host "Backend disabled. Frontend will use mock mode."
    }

    Write-Host "Frontend UI running on http://${frontendHost}:$frontendPort"

    Push-Location frontend
    try {
        pnpm run dev -- --host $frontendHost --port $frontendPort --strictPort
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
    }
}

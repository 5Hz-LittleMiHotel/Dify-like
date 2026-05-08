param(
    [int]$PortWaitSeconds = 60,
    [switch]$Visible
)

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$backendDir = Join-Path $root 'backend'
$frontendDir = Join-Path $root 'frontend'

function Wait-ForTcpPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (Test-NetConnection -ComputerName $HostName -Port $Port -InformationLevel Quiet) {
            return
        }

        Start-Sleep -Seconds 2
    }

    throw "Timed out waiting for $HostName`:$Port"
}

function Start-DevPowerShell {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $windowStyle = if ($Visible) { 'Normal' } else { 'Hidden' }
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass'
    )

    if ($Visible) {
        $arguments += '-NoExit'
    }

    $arguments += @(
        '-Command',
        $Command
    )

    Start-Process -FilePath powershell.exe `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle $windowStyle `
        -PassThru `
        -ArgumentList $arguments
}

Write-Host "Starting Docker stack..."
Push-Location $root
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up -d failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Waiting for PostgreSQL and Redis..."
Wait-ForTcpPort -HostName '127.0.0.1' -Port 5432 -TimeoutSeconds $PortWaitSeconds
Wait-ForTcpPort -HostName '127.0.0.1' -Port 6379 -TimeoutSeconds $PortWaitSeconds

Write-Host "Starting backend and frontend..."
$backendProc = Start-DevPowerShell -WorkingDirectory $backendDir -Command 'conda run --no-capture-output -n dify uvicorn app.main:app --reload --port 8000'
$frontendProc = Start-DevPowerShell -WorkingDirectory $frontendDir -Command 'npm run dev'

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host "Frontend PID: $($frontendProc.Id)"
Write-Host "All services are starting up."

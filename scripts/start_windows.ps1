<#
.SYNOPSIS
    Build (if needed) and start the FinAlly Docker container.

.DESCRIPTION
    PowerShell equivalent of scripts/start_mac.sh.
    Builds the Docker image if it does not exist (or when -Build is passed),
    replaces any existing container, mounts the named data volume, passes the
    project .env file when present, waits for the health check, and prints the
    URL. Idempotent — safe to run multiple times.

.PARAMETER Build
    Force rebuild the Docker image before starting.

.PARAMETER Open
    Open http://localhost:<port> in the default browser after the container starts.

.PARAMETER Port
    Host port to bind to container port 8000. Default: 8000 (or $env:PORT).

.NOTES
    Environment overrides:
      FINALLY_IMAGE_NAME       Docker image name. Default: finally
      FINALLY_CONTAINER_NAME   Docker container name. Default: finally-app
      FINALLY_VOLUME_NAME      Docker volume name. Default: finally-data
      PORT                     Host port. Default: 8000

.EXAMPLE
    .\scripts\start_windows.ps1 -Build -Open -Port 8080
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Open,
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 })
)

$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$ImageName = if ($env:FINALLY_IMAGE_NAME) { $env:FINALLY_IMAGE_NAME } else { 'finally' }
$ContainerName = if ($env:FINALLY_CONTAINER_NAME) { $env:FINALLY_CONTAINER_NAME } else { 'finally-app' }
$VolumeName = if ($env:FINALLY_VOLUME_NAME) { $env:FINALLY_VOLUME_NAME } else { 'finally-data' }

function Test-DockerResource {
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Name
    )
    # Local preference change only affects this scope; quietly probe existence.
    $ErrorActionPreference = 'SilentlyContinue'
    & docker $Kind inspect $Name 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker is required but was not found on PATH.'
    exit 1
}

if (-not (Test-Path (Join-Path $RootDir 'Dockerfile'))) {
    Write-Error "Dockerfile not found at $(Join-Path $RootDir 'Dockerfile')"
    exit 1
}

Set-Location $RootDir

if ($Build -or -not (Test-DockerResource -Kind 'image' -Name $ImageName)) {
    Write-Host "Building Docker image: $ImageName"
    docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'docker build failed.'
        exit 1
    }
}
else {
    Write-Host "Using existing Docker image: $ImageName"
}

if (Test-DockerResource -Kind 'container' -Name $ContainerName) {
    Write-Host "Removing existing container: $ContainerName"
    docker rm -f $ContainerName | Out-Null
}

docker volume create $VolumeName | Out-Null

$envArgs = @()
$EnvFile = Join-Path $RootDir '.env'
if (Test-Path $EnvFile) {
    $envArgs += @('--env-file', $EnvFile)
}
else {
    Write-Host 'No .env file found; starting with built-in defaults.'
}
$envArgs += @('-e', 'DB_PATH=/app/db/finally.db')

Write-Host "Starting container: $ContainerName"
docker run -d `
    --name $ContainerName `
    -p "${Port}:8000" `
    -v "${VolumeName}:/app/db" `
    @envArgs `
    $ImageName | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error 'docker run failed.'
    exit 1
}

$Url = "http://localhost:$Port"
Write-Host "Container started. URL: $Url"

Write-Host 'Waiting for health check...'
for ($i = 0; $i -lt 30; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "Health check passed: $Url/api/health"
            break
        }
    }
    catch {
        # Not ready yet — keep waiting.
    }
    Start-Sleep -Seconds 1
}

if ($Open) {
    Start-Process $Url
}

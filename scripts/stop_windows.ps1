<#
.SYNOPSIS
    Stop and remove the FinAlly Docker container.

.DESCRIPTION
    PowerShell equivalent of scripts/stop_mac.sh.
    Stops and removes the running container if present. Does NOT remove the
    data volume — the SQLite database persists. Idempotent — safe to run
    multiple times.

.NOTES
    Environment overrides:
      FINALLY_CONTAINER_NAME   Docker container name. Default: finally-app
      FINALLY_VOLUME_NAME      Docker volume name. Default: finally-data

.EXAMPLE
    .\scripts\stop_windows.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ContainerName = if ($env:FINALLY_CONTAINER_NAME) { $env:FINALLY_CONTAINER_NAME } else { 'finally-app' }
$VolumeName = if ($env:FINALLY_VOLUME_NAME) { $env:FINALLY_VOLUME_NAME } else { 'finally-data' }

function Test-DockerResource {
    param(
        [Parameter(Mandatory)][string]$Kind,
        [Parameter(Mandatory)][string]$Name
    )
    $ErrorActionPreference = 'SilentlyContinue'
    & docker $Kind inspect $Name 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker is required but was not found on PATH.'
    exit 1
}

if (Test-DockerResource -Kind 'container' -Name $ContainerName) {
    docker stop --time 10 $ContainerName | Out-Null
    docker rm $ContainerName | Out-Null
    Write-Host "Stopped and removed container: $ContainerName"
}
else {
    Write-Host "No container found: $ContainerName"
}

Write-Host "Data volume preserved: $VolumeName"

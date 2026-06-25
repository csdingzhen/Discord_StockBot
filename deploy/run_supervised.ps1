# run_supervised.ps1 -- runs on the deploy machine only.
#
# Polls `origin/main` every $PollIntervalSeconds for new commits. On a new
# HEAD, pulls and runs `docker compose up -d --build`, which rebuilds the
# bot image (pip layer only reinstalls when requirements.txt changed) and
# recreates the container with the new code.
#
# Crash recovery and "come back after the host reboots" are handled by
# Docker itself (`restart: unless-stopped` in docker-compose.yml + Docker
# Desktop's "start on login" setting), not by this script. This script's
# only job is the git-pull -> rebuild step.
#
# Set up once via register_task.ps1 (see deploy/README.md). Do not run this
# on more than one machine at a time -- every running instance would build
# and run its own container logging into Discord with the same bot token.

param(
    [int]$PollIntervalSeconds = 300
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "deploy\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log($message) {
    $line = "$(Get-Date -Format o)  $message"
    Write-Output $line
    Add-Content -Path (Join-Path $LogDir "supervisor.log") -Value $line
}

function Start-BotContainer {
    Write-Log "Running docker compose up -d --build"
    docker compose up -d --build 2>&1 | ForEach-Object { Write-Log "  $_" }
}

function Wait-ForDockerEngine {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        docker info *>$null 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "Docker engine is up."
            return $true
        }
        Write-Log "Docker engine not ready yet, waiting..."
        Start-Sleep -Seconds 5
    }
    Write-Log "Docker engine did not come up within $TimeoutSeconds seconds. Will retry in the poll loop."
    return $false
}

function Test-BotContainerRunning {
    $running = docker compose ps --status running -q bot 2>$null
    return [bool]$running
}

Write-Log "Supervisor starting."
Wait-ForDockerEngine | Out-Null
Start-BotContainer

while ($true) {
    Start-Sleep -Seconds $PollIntervalSeconds

    if (-not (Test-BotContainerRunning)) {
        Write-Log "Bot container is not running (Docker engine was likely still starting up). Retrying."
        if (Wait-ForDockerEngine) {
            Start-BotContainer
        }
        continue
    }

    git fetch origin main --quiet
    $LocalHead = git rev-parse HEAD
    $RemoteHead = git rev-parse origin/main

    if ($LocalHead -ne $RemoteHead) {
        Write-Log "New commits detected ($LocalHead -> $RemoteHead). Pulling."
        git pull origin main --quiet
        Start-BotContainer
    }
}

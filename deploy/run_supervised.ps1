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

Write-Log "Supervisor starting."
Start-BotContainer

while ($true) {
    Start-Sleep -Seconds $PollIntervalSeconds

    git fetch origin main --quiet
    $LocalHead = git rev-parse HEAD
    $RemoteHead = git rev-parse origin/main

    if ($LocalHead -ne $RemoteHead) {
        Write-Log "New commits detected ($LocalHead -> $RemoteHead). Pulling."
        git pull origin main --quiet
        Start-BotContainer
    }
}

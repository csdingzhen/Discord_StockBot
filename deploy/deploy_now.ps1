# deploy_now.ps1 -- manually pull the latest commits and update the bot
# container immediately. Useful while testing, instead of waiting for
# run_supervised.ps1's poll interval.
#
# Since the repo is bind-mounted into the container (see docker-compose.yml),
# a plain code change only needs a restart -- the new files are already
# visible inside the container the moment `git pull` finishes. A full
# rebuild only happens when requirements.txt changed.
#
# Usage:
#   .\deploy\deploy_now.ps1

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ReqPath = Join-Path $RepoRoot "requirements.txt"
$OldHash = if (Test-Path $ReqPath) { (Get-FileHash $ReqPath -Algorithm SHA256).Hash } else { $null }

Write-Output "Pulling latest commits..."
git pull origin main

$NewHash = if (Test-Path $ReqPath) { (Get-FileHash $ReqPath -Algorithm SHA256).Hash } else { $null }

if ($NewHash -ne $OldHash) {
    Write-Output "requirements.txt changed -- rebuilding image..."
    docker compose up -d --build
} else {
    Write-Output "Restarting the bot container..."
    docker compose restart bot
}

Write-Output "Recent bot logs:"
Start-Sleep -Seconds 3
docker compose logs --tail 30 bot

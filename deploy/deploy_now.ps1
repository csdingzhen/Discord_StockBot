# deploy_now.ps1 -- manually pull the latest commits and rebuild/restart the
# bot container immediately. Useful while testing, instead of waiting for
# run_supervised.ps1's poll interval.
#
# Usage:
#   .\deploy\deploy_now.ps1

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Output "Pulling latest commits..."
git pull origin main

Write-Output "Rebuilding and restarting the bot container..."
docker compose up -d --build

Write-Output "Recent bot logs:"
Start-Sleep -Seconds 3
docker compose logs --tail 30 bot

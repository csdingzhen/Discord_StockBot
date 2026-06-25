# register_task.ps1 — run ONCE on the deploy machine to make the git-pull
# supervisor (and therefore the dockerized bot) auto-start at login. Crash
# and reboot recovery of the bot container itself is handled by Docker's
# `restart: unless-stopped` policy + Docker Desktop's "start on login"
# setting, not by this scheduled task -- this task just needs to get the
# supervisor (deploy/run_supervised.ps1) running so it can keep pulling
# new commits and rebuilding.
#
# Usage (on the deploy machine, in a normal PowerShell prompt — no admin needed):
#   .\deploy\register_task.ps1

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $RepoRoot "deploy\run_supervised.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "DiscordStockBot" -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Runs the git-pull supervisor that builds/restarts the dockerized Discord trading bot." -Force

Write-Output "Registered scheduled task 'DiscordStockBot'."
Write-Output "It will start automatically at your next login."
Write-Output "To start it right now: Start-ScheduledTask -TaskName DiscordStockBot"
Write-Output "To check status:       Get-ScheduledTask -TaskName DiscordStockBot"
Write-Output "To stop everything:    Unregister-ScheduledTask -TaskName DiscordStockBot -Confirm:`$false; docker compose -f `"$RepoRoot\docker-compose.yml`" down"

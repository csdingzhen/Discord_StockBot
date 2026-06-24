# register_task.ps1 — run ONCE on the deploy machine to make the bot
# auto-start at login and auto-restart if it crashes or the laptop reboots.
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
    -Description "Runs the Discord trading bot with auto git-pull redeploy and crash recovery." -Force

Write-Output "Registered scheduled task 'DiscordStockBot'."
Write-Output "It will start automatically at your next login."
Write-Output "To start it right now: Start-ScheduledTask -TaskName DiscordStockBot"
Write-Output "To check status:       Get-ScheduledTask -TaskName DiscordStockBot"
Write-Output "To stop everything:    Unregister-ScheduledTask -TaskName DiscordStockBot -Confirm:`$false; Get-CimInstance Win32_Process -Filter `"Name='python.exe'`" | Where-Object { `$_.CommandLine -match 'bot\.py' -and `$_.CommandLine -like '*$RepoRoot*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }"

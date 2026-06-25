# register_nightly_restart_task.ps1 — run ONCE on the deploy machine to
# reboot it every night at 00:00.
#
# This exists purely to exercise/limit long-running state on the host; the
# bot surviving the reboot depends on three other things also being set up
# (see deploy/README.md):
#   1. Windows auto-login (so the box reaches a desktop without anyone
#      physically logging in).
#   2. Docker Desktop -> Settings -> General -> "Start Docker Desktop when
#      you log in".
#   3. The DiscordStockBot scheduled task (register_task.ps1) being
#      registered with an AtLogOn trigger.
#
# Usage (on the deploy machine, in a normal PowerShell prompt — no admin needed):
#   .\deploy\register_nightly_restart_task.ps1

$Action = New-ScheduledTaskAction -Execute "shutdown.exe" `
    -Argument "/r /t 60 /c `"Scheduled nightly restart`""
$Trigger = New-ScheduledTaskTrigger -Daily -At "00:00"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "NightlyHostReboot" -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Reboots the deploy machine at 00:00 daily." -Force

Write-Output "Registered scheduled task 'NightlyHostReboot' (daily at 00:00, 60s warning)."
Write-Output "To check status:    Get-ScheduledTask -TaskName NightlyHostReboot"
Write-Output "To disable it:      Unregister-ScheduledTask -TaskName NightlyHostReboot -Confirm:`$false"

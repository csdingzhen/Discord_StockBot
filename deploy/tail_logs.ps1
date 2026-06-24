# tail_logs.ps1 -- live-follow the bot's logs from any terminal (VSCode's
# included). This only reads log files written by run_supervised.ps1; it
# has no effect on the running supervised process, so closing this
# terminal never stops the bot.
#
# Usage:
#   .\deploy\tail_logs.ps1                 # bot's own stdout (default)
#   .\deploy\tail_logs.ps1 -Which err       # bot's own stderr
#   .\deploy\tail_logs.ps1 -Which supervisor # pull/restart/crash activity

param(
    [ValidateSet("bot", "err", "supervisor")]
    [string]$Which = "bot"
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "deploy\logs"

$File = switch ($Which) {
    "bot"        { "bot.out.log" }
    "err"        { "bot.err.log" }
    "supervisor" { "supervisor.log" }
}

$Path = Join-Path $LogDir $File
if (-not (Test-Path $Path)) {
    Write-Output "No log file yet at $Path -- has the bot started at least once?"
    exit 1
}

Get-Content $Path -Wait -Tail 30

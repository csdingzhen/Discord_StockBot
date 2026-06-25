# tail_logs.ps1 -- live-follow the bot's logs from any terminal (VSCode's
# included). Closing this terminal never stops the bot or the supervisor.
#
# Usage:
#   .\deploy\tail_logs.ps1                  # bot container's stdout/stderr (default)
#   .\deploy\tail_logs.ps1 -Which supervisor # pull/rebuild activity

param(
    [ValidateSet("bot", "supervisor")]
    [string]$Which = "bot"
)

$RepoRoot = Split-Path -Parent $PSScriptRoot

if ($Which -eq "bot") {
    Set-Location $RepoRoot
    docker compose logs -f --tail 50 bot
    return
}

$Path = Join-Path $RepoRoot "deploy\logs\supervisor.log"
if (-not (Test-Path $Path)) {
    Write-Output "No log file yet at $Path -- has the supervisor started at least once?"
    exit 1
}

Get-Content $Path -Wait -Tail 30

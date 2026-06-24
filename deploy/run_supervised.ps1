# run_supervised.ps1 -- runs on the deploy machine only.
#
# Keeps exactly one bot.py process alive: restarts it if it crashes, and
# polls `origin/main` every $PollIntervalSeconds for new commits, pulling
# and restarting on a new HEAD. requirements.txt is reinstalled only when
# it actually changes.
#
# Set up once via register_task.ps1 (see deploy/README.md). Do not run this
# on more than one machine at a time -- every running instance logs into
# Discord with the same bot token and all of them will answer commands.
#
# NOTE: venv's python.exe on Windows is a launcher stub that spawns the
# real interpreter as a CHILD process -- one `bot.py` launch is actually
# two OS processes. Tracking/killing a single PID (e.g. from Start-Process
# -PassThru) only touches the stub and can leave the real child running.
# Everything here finds and stops processes by matching CommandLine
# instead of tracking one PID.

param(
    [int]$PollIntervalSeconds = 300
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot "venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "deploy\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log($message) {
    $line = "$(Get-Date -Format o)  $message"
    Write-Output $line
    Add-Content -Path (Join-Path $LogDir "supervisor.log") -Value $line
}

function Get-FileHashSafe($path) {
    if (Test-Path $path) { return (Get-FileHash $path -Algorithm SHA256).Hash }
    return $null
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -match 'bot\.py' -and $_.CommandLine -like "*$RepoRoot*"
    }
}

function Stop-AllBotProcesses {
    $procs = Get-BotProcesses
    foreach ($p in $procs) {
        Write-Log "Stopping bot.py (PID $($p.ProcessId))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    if ($procs) { Start-Sleep -Seconds 2 }
}

function Start-Bot {
    # Guard against a previous instance somehow still being alive before we start a new one.
    Stop-AllBotProcesses
    Write-Log "Starting bot.py"
    Start-Process -FilePath $VenvPython -ArgumentList "bot.py" -WorkingDirectory $RepoRoot -NoNewWindow `
        -RedirectStandardOutput (Join-Path $LogDir "bot.out.log") `
        -RedirectStandardError (Join-Path $LogDir "bot.err.log")
    Start-Sleep -Seconds 3
}

$RequirementsHash = Get-FileHashSafe (Join-Path $RepoRoot "requirements.txt")
Start-Bot

while ($true) {
    Start-Sleep -Seconds $PollIntervalSeconds

    if (-not (Get-BotProcesses)) {
        Write-Log "bot.py is not running (crashed or never started). Restarting."
        Start-Bot
        continue
    }

    git fetch origin main --quiet
    $LocalHead = git rev-parse HEAD
    $RemoteHead = git rev-parse origin/main

    if ($LocalHead -ne $RemoteHead) {
        Write-Log "New commits detected ($LocalHead -> $RemoteHead). Pulling."
        git pull origin main --quiet

        $NewRequirementsHash = Get-FileHashSafe (Join-Path $RepoRoot "requirements.txt")
        if ($NewRequirementsHash -ne $RequirementsHash) {
            Write-Log "requirements.txt changed -- reinstalling dependencies."
            & $VenvPython -m pip install -r requirements.txt
            $RequirementsHash = $NewRequirementsHash
        }

        Start-Bot
    }
}

# Deploying to a dedicated runtime machine

This bot should run continuously on exactly **one** machine. Running it on
two machines at once means both connect to Discord with the same bot
token and both answer every command — confusing duplicate responses.

Edit and test code on whichever machine you like; the deploy machine just
needs to be running `deploy/run_supervised.ps1`, which polls `origin/main`
and pulls + restarts automatically.

## One-time setup on the deploy machine

1. Clone the repo and install dependencies:
   ```
   git clone https://github.com/csdingzhen/Discord_StockBot.git
   cd Discord_StockBot
   python -m venv venv
   venv\Scripts\pip install -r requirements.txt
   ```
2. Copy your `.env` file into the repo root manually (it's gitignored —
   it will never arrive via `git pull`). If a key rotates later, copy the
   updated `.env` over again by hand.
3. Register the auto-start task:
   ```
   .\deploy\register_task.ps1
   ```
4. Start it now (or just log off/on, or reboot):
   ```
   Start-ScheduledTask -TaskName DiscordStockBot
   ```

## Day-to-day workflow

- Edit code anywhere, commit, `git push` to `origin/main`.
- The deploy machine checks for new commits every 5 minutes by default
  (`-PollIntervalSeconds` param in `run_supervised.ps1`, edit and
  re-register if you want a different interval), pulls, reinstalls
  dependencies only if `requirements.txt` changed, and restarts `bot.py`.
- If `bot.py` crashes between poll cycles, it's restarted on the next
  cycle regardless of whether there were new commits.

## Useful commands on the deploy machine

```
Get-ScheduledTask -TaskName DiscordStockBot          # check it's registered/running
Get-Content deploy\logs\supervisor.log -Tail 30      # supervisor activity (pulls/restarts)
Get-Content deploy\logs\bot.out.log -Tail 50          # bot's own stdout
Get-Content deploy\logs\bot.err.log -Tail 50          # bot's own stderr
Unregister-ScheduledTask -TaskName DiscordStockBot -Confirm:$false   # stop auto-start
```

To fully stop the bot (including the currently running process, not just
future auto-starts), also kill the python process — see the command
printed at the end of `register_task.ps1`, or:
```
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'bot\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

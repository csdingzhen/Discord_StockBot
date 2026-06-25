# Deploying to a dedicated runtime machine

This bot should run continuously on exactly **one** machine. Running it on
two machines at once means both connect to Discord with the same bot
token and both answer every command — confusing duplicate responses.

The bot runs in Docker on the deploy machine. A small PowerShell
supervisor (`deploy/run_supervised.ps1`) polls `origin/main` and rebuilds
the container on new commits. Docker itself (`restart: unless-stopped`)
handles crash recovery, and the deploy machine reboots nightly at 00:00 —
the setup below makes sure it comes back unattended.

## One-time setup on the deploy machine

1. **Install Docker Desktop** and enable
   **Settings → General → "Start Docker Desktop when you log in"**.
   (Docker Desktop is a per-session app on Windows — without this it
   won't be running after a reboot.)

2. **Set up Windows auto-login**, since the box must reach a logged-in
   desktop after the nightly reboot with nobody physically present.
   Use Sysinternals
   [Autologon](https://learn.microsoft.com/en-us/sysinternals/downloads/autologon)
   rather than `netplwiz`/registry — it stores the credential as an
   encrypted LSA secret instead of a near-plaintext registry value.
   Run `Autologon.exe`, enter the Windows account credentials, click Enable.

   This machine's `.env` already holds live API keys, so storing another
   credential (the login password) on disk is a real tradeoff. If that's a
   concern, consider running this under a dedicated low-privilege Windows
   account used only for the bot.

3. **Clone the repo and copy in `.env`**:

   ```bash
   git clone https://github.com/csdingzhen/Discord_StockBot.git
   cd Discord_StockBot
   ```

   Copy your `.env` file into the repo root manually (it's gitignored —
   it will never arrive via `git pull`, and is never baked into the
   Docker image). If a key rotates later, copy the updated `.env` over
   again by hand.

4. **Register the two scheduled tasks**:

   ```powershell
   .\deploy\register_task.ps1                  # starts the git-pull supervisor at login
   .\deploy\register_nightly_restart_task.ps1   # reboots the machine at 00:00
   ```

5. **Bring it up now** (or just log off/on, or reboot) to verify before
   walking away:

   ```bash
   docker compose up -d --build
   ```

## Day-to-day workflow

- Edit code anywhere, commit, `git push` to `origin/main`.
- The deploy machine checks for new commits every 5 minutes by default
  (`-PollIntervalSeconds` param in `run_supervised.ps1`, edit and
  re-register if you want a different interval), pulls, and runs
  `docker compose up -d --build` — the pip layer only reinstalls when
  `requirements.txt` changed.
- If the bot container crashes between poll cycles, Docker restarts it
  immediately on its own (`restart: unless-stopped`) — no need to wait
  for the next poll.
- At 00:00 the machine reboots. Auto-login brings it back to a desktop,
  Docker Desktop starts (per step 1), the container comes back up
  (`unless-stopped`), and the `DiscordStockBot` scheduled task restarts
  the supervisor (`AtLogOn` trigger) to resume polling for commits.

## Useful commands on the deploy machine

```bash
Get-ScheduledTask -TaskName DiscordStockBot              # check supervisor task is registered/running
Get-ScheduledTask -TaskName NightlyHostReboot             # check nightly reboot task
docker compose ps                                         # check the bot container is up
docker compose logs -f --tail 50 bot                      # bot's own stdout/stderr
Get-Content deploy\logs\supervisor.log -Tail 30           # supervisor activity (pulls/rebuilds)
Unregister-ScheduledTask -TaskName DiscordStockBot -Confirm:$false   # stop auto-start of the supervisor
docker compose down                                        # stop the running bot container
```

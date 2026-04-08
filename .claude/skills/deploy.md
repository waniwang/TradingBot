---
description: Deploy code to the Linode server (rsync + migrate + restart)
user_invocable: true
---

# Deploy to Server

Deploy the trading bot code to the Linode server.

## Steps

1. **CRITICAL: You MUST cd first.** Run `cd /Users/hanlin/Developer/Trading/trading-bot && ./bot.sh deploy`
   - `bot.sh` uses `rsync ./` (current directory), so it MUST run from inside `trading-bot/`
   - Running from the repo root will sync the wrong directory and the deploy will silently fail
   - This rsyncs code (excludes .venv, .db, .log, .env), runs DB migrations, and restarts both services
   - It will warn if market is currently open (9:30-4:00 PM ET) and ask for confirmation
2. After deploy completes, run `./bot.sh status` to verify the service is active and heartbeat is fresh
3. Report the results to the user: service status and heartbeat age

## Notes
- Server: `root@172.235.216.175`, remote dir: `/opt/trading-bot`
- Service is managed via systemd: `trading-bot`
- If deploy fails, check SSH connectivity first, then check the error logs with `./bot.sh logs`

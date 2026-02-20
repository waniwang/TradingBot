---
description: Deploy code to the Linode server (rsync + migrate + restart)
user_invocable: true
---

# Deploy to Server

Deploy the trading bot code to the Linode server.

## Steps

1. First, run `cd /Users/hanlin/Developer/Trading/trading-bot && ./bot.sh deploy` to deploy
   - This rsyncs code (excludes .venv, .db, .log, .env), runs DB migrations, and restarts both services
   - It will warn if market is currently open (9:30-4:00 PM ET) and ask for confirmation
2. After deploy completes, run `./bot.sh status` to verify both services are active and heartbeat is fresh
3. Report the results to the user: service status, heartbeat age, and dashboard URL (http://172.235.216.175:8501)

## Notes
- Server: `root@172.235.216.175`, remote dir: `/opt/trading-bot`
- Services are managed via systemd: `trading-bot` and `trading-dashboard`
- If deploy fails, check SSH connectivity first, then check the error logs with `./bot.sh logs`

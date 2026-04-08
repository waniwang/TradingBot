---
description: Check trading bot status (server or local)
user_invocable: true
---

# Check Bot Status

Check the health of the trading bot.

## Steps

1. Run `cd /Users/hanlin/Developer/Trading/trading-bot && ./bot.sh status` to check the server
2. Parse and present the output clearly:
   - Service status (active/stopped) for `trading-bot`
   - Current phase (premarket_scan, intraday_monitor, idle, etc.)
   - Heartbeat freshness (if >120s, flag as STALE — bot may be down)
   - Next scheduled job and time
   - Watchlist size
3. If the user asks about local status, use `./bot.sh local status` instead

## If something looks wrong
- Heartbeat stale (>120s): suggest checking logs with `./bot.sh logs`
- Service stopped: suggest `./bot.sh start` or `./bot.sh restart`

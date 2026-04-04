# Bot Operations Guide

All commands run from the `trading-bot/` directory.

## Server (Linode — always on)

```bash
./bot.sh status          # health check: phase, heartbeat, next job
./bot.sh start           # start bot + dashboard
./bot.sh stop            # stop both
./bot.sh restart         # restart both
./bot.sh logs            # live log stream (Ctrl+C to exit)
./bot.sh deploy          # push local code changes and restart
./bot.sh scan            # trigger manual premarket scan
./bot.sh verify          # run daily verification (last trading day)
./bot.sh verify 2026-03-20   # verify specific date
```

Dashboard: Vercel (frontend) + `http://172.235.216.175:8000/api` (API)

## Local (Mac — for development/testing)

```bash
./bot.sh local status    # health check
./bot.sh local start     # start bot + dashboard in background
./bot.sh local stop      # stop both
./bot.sh local restart   # restart both
./bot.sh local logs      # live log stream (Ctrl+C to exit)
./bot.sh local verify    # run daily verification locally
```

Dashboard: `http://localhost:3000` (frontend) + `http://localhost:8000/api` (API)

## Typical workflows

**Check if everything is fine:**
```bash
./bot.sh status
```

**Deploy a code change:**
```bash
./bot.sh deploy
```

**Debug something locally before deploying:**
```bash
./bot.sh local start
./bot.sh local logs
# ... make changes ...
./bot.sh local stop
./bot.sh deploy
```

**View server logs after a trading session:**
```bash
./bot.sh logs
```

**Trigger a manual scan (outside scheduled hours):**
```bash
./bot.sh scan
```
This creates a `trigger_scan` file that the heartbeat loop detects. The premarket scan runs in a background thread with `force=True` (bypasses trading-day check).

**Run daily verification:**
```bash
./bot.sh verify              # verifies last trading day
./bot.sh verify 2026-03-20   # verifies specific date
./bot.sh local verify        # run locally
```

---

## Scheduled Jobs

The bot runs scheduled jobs (all times Eastern):

| Time | Job | Description |
|------|-----|-------------|
| 5:00 PM (prior day) | Breakout nightly scan | Momentum rank 1,500 stocks via yfinance, consolidation analysis |
| 6:00 AM | Premarket scan | EP gappers, promote breakout candidates, pre-fetch daily bars |
| 9:25 AM | Finalize watchlist | Subscribe to Alpaca real-time 1m bars for all active candidates |
| 9:30 AM | Intraday monitor | Start signal evaluation on incoming 1m bars |
| 3:00 PM | EP swing scan | EP earnings + EP news scanners + strategy A/B evaluation |
| 3:50 PM | EP swing execute | Place limit entries for EP earnings + EP news near close |
| 3:55 PM | EOD tasks | Trailing stop updates, MA-close exits, daily P&L, Telegram summary |
| Every 5 min (9-15h Mon-Fri) | Reconcile positions | Poll broker for GTC stop fills, detect unprotected positions |
| Every 30s | Heartbeat | Write bot_status.json (phase, next job, progress) for dashboard |

---

## Troubleshooting

**Bot shows "idle" phase during market hours:**
- Check logs for errors: `./bot.sh logs`
- Verify the watchlist was populated: look for "PRE-MARKET SCAN DONE: N candidates" in logs
- If watchlist is empty, no stream subscription happens and no signals fire

**No trades executing:**
- Check `docs/daily-verification.md` → "Diagnostic: Where Trades Get Blocked"
- Common causes: strict consolidation scanner, high RVOL thresholds, tight extension guards
- Run `./bot.sh verify` to see automated check results

**Dashboard not updating:**
- Check heartbeat: `bot_status.json` should update every 30 seconds
- If stale, the bot process may have crashed: `./bot.sh status`
- Frontend is Next.js on Vercel; API is FastAPI on Linode port 8000

**Telegram alerts not arriving:**
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set correctly
- Check logs for "Telegram send failed" warnings
- Test with @BotFather: send `/start` to your bot

**Unprotected position alert on startup:**
- The bot detected an open position with no broker stop order
- This can happen if the bot crashed between placing an entry and placing the stop
- Manually place a stop order at the price shown in the alert

---
description: View trading bot logs (server or local) and help debug issues
user_invocable: true
---

# View Bot Logs

View and analyze trading bot logs.

## Steps

1. Ask the user if they want server logs or local logs (default: server)
2. For server logs: run `cd /Users/hanlin/Developer/Trading/trading-bot && ssh -o StrictHostKeyChecking=no root@172.235.216.175 "tail -100 /opt/trading-bot/trading_bot.log"`
3. For local logs: run `tail -100 /Users/hanlin/Developer/Trading/trading-bot/trading_bot.log`
4. Analyze the output for:
   - Errors or exceptions (look for ERROR, Exception, Traceback)
   - Recent job executions (premarket scan, intraday monitor, EOD tasks)
   - Any trading activity (signals fired, orders placed, stops hit)
5. Summarize findings clearly

## Common issues to look for
- `ConnectionError` / `APIError`: Alpaca API issues (check API keys, rate limits)
- `yfinance` download failures: network or Yahoo throttling
- Stale heartbeat: scheduler may have crashed
- DB errors: check if migrations are up to date (`alembic upgrade head`)

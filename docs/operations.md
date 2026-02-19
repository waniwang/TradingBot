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
```

Dashboard: http://172.235.216.175:8501

## Local (Mac — for development/testing)

```bash
./bot.sh local status    # health check
./bot.sh local start     # start bot + dashboard in background
./bot.sh local stop      # stop both
./bot.sh local restart   # restart both
./bot.sh local logs      # live log stream (Ctrl+C to exit)
```

Dashboard: http://localhost:8501

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

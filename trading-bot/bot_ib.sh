#!/usr/bin/env bash
# bot_ib.sh — Manage the personal IBKR paper-trading bot (EP earnings + EP news).
#
# Runs alongside the shared Alpaca bot. Uses the same codebase but a separate
# config (config.ib.local.yaml — gitignored), DB (trading_bot_ib.db), log
# (trading_bot_ib.log), heartbeat (bot_status_ib.json), and API port (8001).
#
# Usage:
#   ./bot_ib.sh start     — start main_ib.py + API on :8001
#   ./bot_ib.sh stop      — stop both
#   ./bot_ib.sh restart   — restart both
#   ./bot_ib.sh status    — show process state + heartbeat + next job
#   ./bot_ib.sh logs      — tail the bot log
#
# Prereqs:
#   - IB Gateway running and logged in to PAPER account on 127.0.0.1:4002
#   - trading-bot/config.ib.local.yaml exists with `ibkr:` + `database:` sections
#     and your personal risk / share-size tweaks
#   - .venv has ib_async, exchange_calendars installed
#
# To launch the dashboard that talks to this bot:
#   cd dashboard && NEXT_PUBLIC_API_URL=http://localhost:8001/api PORT=3001 npm run dev

set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR"

VENV_PY="$BOT_DIR/.venv/bin/python"
VENV_UVICORN="$BOT_DIR/.venv/bin/uvicorn"

BOT_PID="$BOT_DIR/.bot_ib.pid"
API_PID="$BOT_DIR/.api_ib.pid"
BOT_LOG="$BOT_DIR/trading_bot_ib.log"
API_LOG="$BOT_DIR/api_ib.log"
STATUS_FILE="$BOT_DIR/bot_status_ib.json"

# Config the personal instance loads. Both the bot (main_ib.py) and the API
# (shared api.main:app) pick up this file via BOT_CONFIG.
BOT_CONFIG_FILE="${BOT_CONFIG:-config.ib.local.yaml}"
API_PORT="${API_PORT:-8001}"

# Sanity check before starting anything
check_config() {
  if [ ! -f "$BOT_DIR/$BOT_CONFIG_FILE" ]; then
    echo "ERROR: config file '$BOT_CONFIG_FILE' not found in $BOT_DIR"
    echo "Create one by copying the shared config and editing the ibkr/database sections:"
    echo "  cp $BOT_DIR/config.yaml $BOT_DIR/$BOT_CONFIG_FILE"
    exit 1
  fi
}

start() {
  check_config
  echo "==> Starting IBKR bot (config: $BOT_CONFIG_FILE)"

  if [ -f "$BOT_PID" ] && kill -0 "$(cat "$BOT_PID")" 2>/dev/null; then
    echo "    Bot already running (PID $(cat "$BOT_PID"))"
  else
    BOT_CONFIG="$BOT_CONFIG_FILE" \
      "$VENV_PY" main_ib.py >> "$BOT_LOG" 2>&1 &
    echo $! > "$BOT_PID"
    echo "    main_ib.py started (PID $!)"
  fi

  if [ -f "$API_PID" ] && kill -0 "$(cat "$API_PID")" 2>/dev/null; then
    echo "    API already running (PID $(cat "$API_PID"))"
  else
    BOT_CONFIG="$BOT_CONFIG_FILE" \
    BOT_STATUS_FILE="bot_status_ib.json" \
    BOT_BROKER="ibkr" \
      "$VENV_UVICORN" api.main:app --host 0.0.0.0 --port "$API_PORT" \
      >> "$API_LOG" 2>&1 &
    echo $! > "$API_PID"
    echo "    API started on :$API_PORT (PID $!)"
  fi

  echo
  echo "    Logs:      tail -f $BOT_LOG"
  echo "    API:       http://localhost:$API_PORT/api/doctor"
  echo "    Dashboard: cd ../dashboard && NEXT_PUBLIC_API_URL=http://localhost:$API_PORT/api PORT=3001 npm run dev"
}

stop() {
  for name in BOT API; do
    pf=$([ "$name" = "BOT" ] && echo "$BOT_PID" || echo "$API_PID")
    if [ -f "$pf" ]; then
      pid=$(cat "$pf")
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "    $name stopped (PID $pid)"
      else
        echo "    $name stale PID, cleaning up"
      fi
      rm -f "$pf"
    else
      echo "    $name not running"
    fi
  done
}

status() {
  echo "==> IBKR bot status (config: $BOT_CONFIG_FILE)"
  for name in BOT API; do
    pf=$([ "$name" = "BOT" ] && echo "$BOT_PID" || echo "$API_PID")
    if [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null; then
      echo "    $name: active (PID $(cat "$pf"))"
    else
      echo "    $name: inactive"
    fi
  done

  if [ -f "$STATUS_FILE" ]; then
    echo
    echo "==> Heartbeat ($STATUS_FILE):"
    "$VENV_PY" -c "
import json
from datetime import datetime, timezone
with open('$STATUS_FILE') as f: s=json.load(f)
hb=s.get('last_heartbeat')
age='n/a'
if hb:
    try:
        dt=datetime.fromisoformat(hb)
        age=f'{int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())}s ago'
    except Exception as e: age=str(e)
print(f'    phase:       {s.get(\"phase\")}')
print(f'    broker:      {s.get(\"broker\")}')
print(f'    environment: {s.get(\"environment\")}')
print(f'    heartbeat:   {age}')
print(f'    next_job:    {s.get(\"next_job\")} @ {s.get(\"next_job_time\")}')"
  fi
}

logs() {
  echo "==> Tailing $BOT_LOG (Ctrl+C to exit)"
  tail -f "$BOT_LOG"
}

case "${1:-}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; sleep 1; start ;;
  status)  status ;;
  logs)    logs ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac

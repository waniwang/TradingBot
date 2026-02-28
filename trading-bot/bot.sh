#!/bin/bash
# bot.sh — Manage the trading bot locally or on the Linode server
#
# Usage:
#   ./bot.sh [local|server] {status|start|stop|restart|logs}
#   ./bot.sh deploy         — push local code to server and restart
#
# Target defaults to "server" if omitted:
#   ./bot.sh status              → server status
#   ./bot.sh local status        → local status
#   ./bot.sh server status       → server status (explicit)

SERVER="root@172.235.216.175"
SSH="ssh -o StrictHostKeyChecking=no $SERVER"
REMOTE_DIR="/opt/trading-bot"

# PID files for local mode
BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_PID="$BOT_DIR/.bot.pid"
DASH_PID="$BOT_DIR/.dashboard.pid"

# Parse optional target argument
if [[ "$1" == "local" || "$1" == "server" ]]; then
  target="$1"
  cmd="${2:-status}"
else
  target="server"
  cmd="${1:-status}"
fi

# ---------------------------------------------------------------------------
# LOCAL helpers
# ---------------------------------------------------------------------------

local_start() {
  # Run DB migrations before starting
  echo "==> Running DB migrations..."
  cd "$BOT_DIR"
  source .env 2>/dev/null || true
  .venv/bin/alembic upgrade head 2>&1 | grep -v "^$"

  # Check if already running
  if [ -f "$BOT_PID" ] && kill -0 "$(cat "$BOT_PID")" 2>/dev/null; then
    echo "Bot is already running (PID $(cat "$BOT_PID"))"
  else
    echo "==> Starting bot..."
    cd "$BOT_DIR"
    source .env 2>/dev/null || true
    .venv/bin/python main.py >> trading_bot.log 2>&1 &
    echo $! > "$BOT_PID"
    echo "    Bot started (PID $!)"
  fi

  if [ -f "$DASH_PID" ] && kill -0 "$(cat "$DASH_PID")" 2>/dev/null; then
    echo "Dashboard is already running (PID $(cat "$DASH_PID"))"
  else
    echo "==> Starting dashboard..."
    cd "$BOT_DIR"
    source .env 2>/dev/null || true
    .venv/bin/streamlit run dashboard/app.py \
      --server.port 8501 --server.headless true \
      --browser.gatherUsageStats false >> dashboard.log 2>&1 &
    echo $! > "$DASH_PID"
    echo "    Dashboard started (PID $!)"
  fi

  echo ""
  echo "Dashboard: http://localhost:8501"
}

local_stop() {
  if [ -f "$BOT_PID" ]; then
    pid=$(cat "$BOT_PID")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Bot stopped (PID $pid)"
    else
      echo "Bot was not running"
    fi
    rm -f "$BOT_PID"
  else
    echo "Bot was not running"
  fi

  if [ -f "$DASH_PID" ]; then
    pid=$(cat "$DASH_PID")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "Dashboard stopped (PID $pid)"
    else
      echo "Dashboard was not running"
    fi
    rm -f "$DASH_PID"
  else
    echo "Dashboard was not running"
  fi
}

local_status() {
  echo "=== Local Bot Status ==="

  if [ -f "$BOT_PID" ] && kill -0 "$(cat "$BOT_PID")" 2>/dev/null; then
    echo "  trading-bot:       active (PID $(cat "$BOT_PID"))"
  else
    echo "  trading-bot:       stopped"
  fi

  if [ -f "$DASH_PID" ] && kill -0 "$(cat "$DASH_PID")" 2>/dev/null; then
    echo "  trading-dashboard: active (PID $(cat "$DASH_PID"))"
  else
    echo "  trading-dashboard: stopped"
  fi

  echo ""

  if [ -f "$BOT_DIR/bot_status.json" ]; then
    python3 -c "
import json, datetime
d = json.load(open('$BOT_DIR/bot_status.json'))
hb = d.get('last_heartbeat','')
if hb:
    dt = datetime.datetime.fromisoformat(hb)
    age = (datetime.datetime.now(datetime.timezone.utc) - dt.astimezone(datetime.timezone.utc)).total_seconds()
    stale = ' (STALE)' if age > 120 else f' ({int(age)}s ago)'
else:
    stale = ''
print(f\"  Phase:      {d.get('phase','?')}\")
print(f\"  Heartbeat:  {hb}{stale}\")
print(f\"  Next job:   {d.get('next_job','?')} at {d.get('next_job_time','?')}\")
"
  fi

  echo ""
  echo "  Dashboard: http://localhost:8501"
}

local_logs() {
  echo "==> Tailing local bot logs (Ctrl+C to exit)..."
  tail -f "$BOT_DIR/trading_bot.log"
}

# ---------------------------------------------------------------------------
# SERVER helpers
# ---------------------------------------------------------------------------

server_status() {
  echo "=== Server Bot Status ==="
  $SSH bash << 'REMOTE'
  bot=$(systemctl is-active trading-bot)
  dash=$(systemctl is-active trading-dashboard)
  echo "  trading-bot:       $bot"
  echo "  trading-dashboard: $dash"
  echo ""
  if [ -f /opt/trading-bot/bot_status.json ]; then
    python3 -c "
import json, datetime, sys
d = json.load(open('/opt/trading-bot/bot_status.json'))
hb = d.get('last_heartbeat','')
if hb:
    dt = datetime.datetime.fromisoformat(hb)
    age = (datetime.datetime.now(datetime.timezone.utc) - dt.astimezone(datetime.timezone.utc)).total_seconds()
    stale = ' (STALE — bot may be down)' if age > 120 else f' ({int(age)}s ago)'
else:
    stale = ''
print(f\"  Phase:      {d.get('phase','?')}\")
print(f\"  Heartbeat:  {hb}{stale}\")
print(f\"  Next job:   {d.get('next_job','?')} at {d.get('next_job_time','?')}\")
"
  fi
  echo ""
  echo "  Dashboard: http://172.235.216.175:8501"
REMOTE
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if [[ "$target" == "local" ]]; then
  case "$cmd" in
    status)  local_status ;;
    start)   local_start ;;
    stop)    local_stop ;;
    restart) local_stop; sleep 1; local_start ;;
    logs)    local_logs ;;
    verify)
      echo "==> Running daily verification..."
      cd "$BOT_DIR"
      source .env 2>/dev/null || true
      .venv/bin/python verify_day.py ${@:3}
      ;;
    *) echo "Usage: $0 local {status|start|stop|restart|logs|verify}"; exit 1 ;;
  esac
else
  case "$cmd" in
    status)
      server_status
      ;;
    start)
      echo "==> Starting services..."
      $SSH "systemctl start trading-bot trading-dashboard"
      sleep 3
      $SSH "systemctl is-active trading-bot trading-dashboard"
      echo "Done. Dashboard: http://172.235.216.175:8501"
      ;;
    stop)
      echo "==> Stopping services..."
      $SSH "systemctl stop trading-bot trading-dashboard"
      $SSH "systemctl is-active trading-bot trading-dashboard" 2>/dev/null || echo "Both stopped."
      ;;
    restart)
      echo "==> Restarting services..."
      $SSH "systemctl restart trading-bot trading-dashboard"
      sleep 3
      $SSH "systemctl is-active trading-bot trading-dashboard"
      echo "Done."
      ;;
    scan)
      echo "==> Triggering manual scan on server..."
      $SSH "touch $REMOTE_DIR/trigger_scan"
      echo "    Trigger file created. Scan will start within 30s (next heartbeat)."
      echo "    Use './bot.sh logs' to watch progress."
      ;;
    logs)
      echo "==> Tailing server logs (Ctrl+C to exit)..."
      $SSH "tail -f $REMOTE_DIR/trading_bot.log"
      ;;
    deploy)
      # Warn if deploying during market hours (9:30–16:00 ET Mon–Fri)
      is_market_hours=$(python3 -c "
from datetime import datetime
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo('America/New_York'))
weekday = now.weekday()  # 0=Mon, 4=Fri
hour, minute = now.hour, now.minute
in_hours = weekday < 5 and (hour, minute) >= (9, 30) and (hour, minute) < (16, 0)
print('yes' if in_hours else 'no')
" 2>/dev/null || echo "no")

      if [[ "$is_market_hours" == "yes" ]]; then
        echo "⚠️  WARNING: Market is currently open (9:30–4:00 PM ET)."
        echo "   Restarting now will interrupt any active trades or monitoring."
        read -r -p "   Deploy anyway? [y/N] " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Deploy cancelled."; exit 0; }
      fi

      echo "==> Syncing code to $SERVER..."
      rsync -az \
        --exclude='.venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.db' \
        --exclude='*.log' \
        --exclude='bot_status.json' \
        --exclude='.env' \
        -e "ssh -o StrictHostKeyChecking=no" \
        ./ "$SERVER:$REMOTE_DIR/"

      echo "==> Running DB migrations..."
      $SSH "cd $REMOTE_DIR && .venv/bin/alembic upgrade head"

      echo "==> Restarting bot + dashboard..."
      $SSH "systemctl restart trading-bot trading-dashboard"
      sleep 4

      echo "==> Recent logs:"
      $SSH "tail -8 $REMOTE_DIR/trading_bot.log"
      echo ""
      echo "Done. Dashboard: http://172.235.216.175:8501"
      ;;
    verify)
      echo "==> Running daily verification on server..."
      $SSH "cd $REMOTE_DIR && source .env 2>/dev/null; .venv/bin/python verify_day.py ${@:2}"
      ;;
    *)
      echo "Usage: $0 [local|server] {status|start|stop|restart|logs|scan|verify}"
      echo "       $0 deploy"
      exit 1
      ;;
  esac
fi

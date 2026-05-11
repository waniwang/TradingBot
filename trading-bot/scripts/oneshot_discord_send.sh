#!/bin/bash
# One-shot: SSH to the production Linode server, read today's EP earnings +
# EP news watchlist rows, pipe to oneshot_discord_summary.py for headline
# enrichment + Discord post.
#
# Usage:
#   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
#     ./scripts/oneshot_discord_send.sh
#
# Optional:
#   FINNHUB_API_KEY=...   Enables Finnhub fallback when yfinance returns no headline
#   PYTHON=...            Override the local Python interpreter (defaults to repo .venv)
#
# Read-only on the server (single SQL query, no service restart, no code copy).
# Safe to run any time after the 3:00/3:05 PM ET scans complete.

set -euo pipefail

if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
  echo "ERROR: DISCORD_WEBHOOK_URL env var must be set" >&2
  exit 2
fi

SERVER="root@172.235.216.175"
REMOTE_DIR="/opt/trading-bot/trading-bot"

# Find a usable local Python. Prefer the main repo's venv (where deps are
# installed); fall back to whatever's on PATH.
PY="${PYTHON:-/Users/sharonk/Documents/TradingBot/trading-bot/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "ERROR: no Python interpreter found" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SCRIPT="$SCRIPT_DIR/oneshot_discord_summary.py"

# Server-side dump: query today's ready/watching EP rows, emit JSON to stdout.
REMOTE_QUERY=$(cat <<'PYEOF'
import json
from datetime import datetime
import pytz
from db.models import init_db, get_session, Watchlist
ET = pytz.timezone("America/New_York")
today = datetime.now(ET).date()
engine = init_db("sqlite:///trading_bot.db")
with get_session(engine) as s:
    rows = (
        s.query(Watchlist)
        .filter(
            Watchlist.scan_date == today,
            Watchlist.stage.in_(["ready", "watching"]),
            Watchlist.setup_type.in_(["ep_earnings", "ep_news"]),
        )
        .all()
    )
    out = [
        {
            "ticker": r.ticker,
            "setup_type": r.setup_type,
            "stage": r.stage,
            "meta": r.meta,
        }
        for r in rows
    ]
print(json.dumps(out))
PYEOF
)

echo "Querying server for today's EP candidates..." >&2
JSON=$(ssh -o StrictHostKeyChecking=no "$SERVER" \
  "cd $REMOTE_DIR && .venv/bin/python -c \"$REMOTE_QUERY\"")

echo "Server returned: $JSON" >&2

echo "Posting to Discord..." >&2
echo "$JSON" | DISCORD_WEBHOOK_URL="$DISCORD_WEBHOOK_URL" \
  FINNHUB_API_KEY="${FINNHUB_API_KEY:-}" \
  "$PY" "$LOCAL_SCRIPT"

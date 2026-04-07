#!/usr/bin/env bash
# Health check monitor — runs via cron, alerts on bot outages.
#
# Curls /api/doctor and sends a Telegram alert when the bot transitions
# to critical/degraded. Uses a state file to avoid spamming — only alerts
# on state changes and sends a recovery notice when the bot comes back.
#
# Cron (every 12 hours):
#   0 */12 * * * /opt/trading-bot/scripts/health_check.sh >> /var/log/health_check.log 2>&1

set -euo pipefail

DOCTOR_URL="http://localhost:8000/api/doctor"
STATE_FILE="/tmp/trading_bot_health_state"

# Load Telegram credentials from .env
ENV_FILE="/opt/trading-bot/.env"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

send_telegram() {
    local message="$1"
    if [[ -z "$TELEGRAM_BOT_TOKEN" || -z "$TELEGRAM_CHAT_ID" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') [WARN] Telegram not configured — skipping alert"
        return 0
    fi
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$message" \
        -d parse_mode="Markdown" > /dev/null 2>&1
}

# Fetch doctor endpoint
response=$(curl -s --connect-timeout 5 --max-time 10 "$DOCTOR_URL" 2>/dev/null) || {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Cannot reach API at $DOCTOR_URL"
    # API itself is down — this is critical
    prev_state=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")
    if [[ "$prev_state" != "api_down" ]]; then
        echo "api_down" > "$STATE_FILE"
        send_telegram "🚨 *TRADING BOT CRITICAL*
API server is unreachable — both bot and API may be down.
Check server: \`ssh root@172.235.216.175\`"
    fi
    exit 1
}

# Parse status from JSON
status=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null) || {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [ERROR] Cannot parse doctor response"
    exit 1
}
summary=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null)

prev_state=$(cat "$STATE_FILE" 2>/dev/null || echo "unknown")
echo "$status" > "$STATE_FILE"

echo "$(date '+%Y-%m-%d %H:%M:%S') [INFO] Health: $status — $summary"

# Alert on state transitions
if [[ "$status" == "critical" && "$prev_state" != "critical" ]]; then
    send_telegram "🚨 *TRADING BOT DOWN*
$summary

Check: \`ssh root@172.235.216.175 'journalctl -u trading-bot --since \"5 min ago\" --no-pager'\`"
elif [[ "$status" == "degraded" && "$prev_state" != "degraded" ]]; then
    send_telegram "⚠️ *TRADING BOT DEGRADED*
$summary"
elif [[ "$status" == "healthy" && "$prev_state" != "healthy" && "$prev_state" != "unknown" ]]; then
    send_telegram "✅ *TRADING BOT RECOVERED*
All checks passed."
fi

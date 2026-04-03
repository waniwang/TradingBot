#!/bin/bash
# server-deploy.sh — Called by GitHub Actions (or manually via bot.sh deploy)
# Pulls latest code, runs migrations, restarts services only if already active.
#
# Usage: /opt/trading-bot/scripts/server-deploy.sh

set -euo pipefail

DEPLOY_DIR="/opt/trading-bot"
LOG_FILE="$DEPLOY_DIR/deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cd "$DEPLOY_DIR"

# --- 1. Pull latest code ---
log "Pulling latest code from origin/main..."
git fetch origin main
git reset --hard origin/main
log "Code updated to $(git rev-parse --short HEAD)"

# --- 2. Run DB migrations ---
log "Running alembic migrations..."
source .env 2>/dev/null || true
.venv/bin/alembic upgrade head 2>&1 | tee -a "$LOG_FILE"
log "Migrations complete."

# --- 3. Conditional restart ---
bot_status=$(systemctl is-active trading-bot 2>/dev/null || echo "inactive")
dash_status=$(systemctl is-active trading-dashboard 2>/dev/null || echo "inactive")

if [ "$bot_status" = "active" ]; then
    log "trading-bot is active -> restarting..."
    systemctl restart trading-bot
    log "trading-bot restarted."
else
    log "trading-bot is $bot_status -> skipping restart."
fi

if [ "$dash_status" = "active" ]; then
    log "trading-dashboard is active -> restarting..."
    systemctl restart trading-dashboard
    log "trading-dashboard restarted."
else
    log "trading-dashboard is $dash_status -> skipping restart."
fi

# --- 4. Summary ---
log "Deploy complete. Bot: $bot_status -> $(systemctl is-active trading-bot 2>/dev/null || echo inactive), Dashboard: $dash_status -> $(systemctl is-active trading-dashboard 2>/dev/null || echo inactive)"

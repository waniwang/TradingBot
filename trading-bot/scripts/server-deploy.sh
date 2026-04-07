#!/bin/bash
# server-deploy.sh — Called by GitHub Actions (or manually via bot.sh deploy)
# Pulls latest code, runs migrations, restarts services only if already active.
#
# Usage: /opt/trading-bot/scripts/server-deploy.sh

set -euo pipefail

REPO_DIR="/opt/trading-bot"
APP_DIR="$REPO_DIR/trading-bot"
LOG_FILE="$APP_DIR/deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# --- 1. Pull latest code ---
cd "$REPO_DIR"
log "Pulling latest code from origin/main..."
git fetch origin main
git reset --hard origin/main
log "Code updated to $(git rev-parse --short HEAD)"

# --- 1b. Sync trading-bot/ contents to working root ---
# The git repo has code under trading-bot/ but services run from /opt/trading-bot/
log "Syncing trading-bot/ to working root..."
rsync -a --exclude=".venv" --exclude="__pycache__" --exclude="*.db" --exclude="*.log" \
    --exclude=".env" --exclude="config.yaml" --exclude="bot_status.json" \
    --exclude="trading-bot" \
    --exclude=".git" --exclude="dashboard" --exclude="docs" --exclude=".github" \
    trading-bot/ .

# --- 2. Run DB migrations (skip if alembic not installed) ---
cd "$APP_DIR"
source .env 2>/dev/null || true
if [ -f .venv/bin/alembic ]; then
    log "Running alembic migrations..."
    .venv/bin/alembic upgrade head 2>&1 | tee -a "$LOG_FILE"
    log "Migrations complete."
else
    log "alembic not installed, skipping migrations."
fi

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

# --- 4. Restart API if active ---
api_status=$(systemctl is-active trading-api 2>/dev/null || echo "inactive")
if [ "$api_status" = "active" ]; then
    log "trading-api is active -> restarting..."
    systemctl restart trading-api
    log "trading-api restarted."
else
    log "trading-api is $api_status -> skipping restart."
fi

# --- 5. Summary ---
log "Deploy complete. Bot: $bot_status -> $(systemctl is-active trading-bot 2>/dev/null || echo inactive), Dashboard: $dash_status -> $(systemctl is-active trading-dashboard 2>/dev/null || echo inactive), API: $api_status -> $(systemctl is-active trading-api 2>/dev/null || echo inactive)"

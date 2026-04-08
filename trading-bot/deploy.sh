#!/bin/bash
# deploy.sh — Push local code changes to the Linode server
#
# Usage: ./deploy.sh
#
# What it does:
#   1. Rsyncs code (excludes venv, db, logs, .env)
#   2. Restarts trading-bot service
#   3. Shows last 10 lines of log to confirm startup

set -e

SERVER="root@172.235.216.175"
REMOTE_DIR="/opt/trading-bot"

echo "==> Syncing code to $SERVER..."
rsync -avz \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='*.log' \
  --exclude='bot_status.json' \
  --exclude='.env' \
  -e "ssh -o StrictHostKeyChecking=no" \
  ./ "$SERVER:$REMOTE_DIR/"

echo "==> Restarting trading-bot service..."
ssh -o StrictHostKeyChecking=no "$SERVER" "systemctl restart trading-bot"

echo "==> Waiting for startup..."
sleep 4

echo "==> Recent logs:"
ssh -o StrictHostKeyChecking=no "$SERVER" "tail -10 $REMOTE_DIR/trading_bot.log"

echo ""
echo "==> Service status:"
ssh -o StrictHostKeyChecking=no "$SERVER" "systemctl is-active trading-bot"

echo ""
echo "Done."

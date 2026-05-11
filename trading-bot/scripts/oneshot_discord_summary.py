"""One-shot: read EP candidates JSON from stdin, post a summary to Discord.

Used to send today's Discord candidate summary BEFORE the 3:10 PM scheduled
job (job_discord_candidate_summary) is deployed to the production server.
The full feature ships with main.py's scheduler; this script replicates
the same code path manually for a single ad-hoc run.

Usage:
    ssh root@172.235.216.175 'cd /opt/trading-bot/trading-bot && \\
      .venv/bin/python -c "<dump query>"' \\
      | DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \\
        FINNHUB_API_KEY=optional \\
        .venv/bin/python scripts/oneshot_discord_summary.py

stdin format: JSON array of objects with keys
    ticker (str), setup_type (str), stage (str), meta (dict)
which is the exact shape job_discord_candidate_summary builds in main.py.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

# Ensure the trading-bot dir is on sys.path so the core.* imports resolve when
# this script is invoked from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discord import format_candidate_summary, make_discord_notifier
from core.news import fetch_headline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("oneshot_discord_summary")


JOB_TIMEOUT_SEC = 60.0


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL env var is not set", file=sys.stderr)
        return 2

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip() or None

    raw = sys.stdin.read()
    if not raw.strip():
        print("ERROR: stdin is empty (expected JSON array of candidates)", file=sys.stderr)
        return 2

    try:
        candidates = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: failed to parse stdin as JSON: {e}", file=sys.stderr)
        print("First 200 chars of stdin:", file=sys.stderr)
        print(raw[:200], file=sys.stderr)
        return 2

    if not isinstance(candidates, list):
        print(f"ERROR: expected JSON array, got {type(candidates).__name__}", file=sys.stderr)
        return 2

    notify_discord = make_discord_notifier(webhook_url)

    if not candidates:
        logger.info("No candidates in stdin; sending empty-state Discord message")
        notify_discord(format_candidate_summary([]))
        return 0

    logger.info(
        "Fetching headlines for %d candidate(s) (finnhub_fallback=%s)",
        len(candidates),
        bool(finnhub_key),
    )

    started = time.monotonic()
    enriched: list[dict] = []
    for c in candidates:
        if time.monotonic() - started > JOB_TIMEOUT_SEC:
            logger.warning(
                "60s wall-clock cap hit at %s; remaining tickers will get no headline",
                c.get("ticker", "?"),
            )
            c["headline"] = None
        else:
            ticker = c.get("ticker")
            if not ticker:
                c["headline"] = None
            else:
                c["headline"] = fetch_headline(ticker, finnhub_key=finnhub_key)
        enriched.append(c)

    msg = format_candidate_summary(enriched)
    logger.info("Posting %d-char message to Discord", len(msg))
    print(msg, file=sys.stderr)  # echo to stderr so user sees it locally too
    notify_discord(msg)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

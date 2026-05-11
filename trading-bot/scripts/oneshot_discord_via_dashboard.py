"""One-shot: fetch today's EP candidates from the Vercel dashboard proxy
and post a summary to Discord.

Use this when you don't have SSH to the Linode server. The Vercel
dashboard's Next.js middleware adds the API key server-side, so the
public Vercel URL is reachable from any computer without auth.

Usage:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \\
      [FINNHUB_API_KEY=optional] \\
      .venv/bin/python scripts/oneshot_discord_via_dashboard.py

Filters to EP earnings + EP news rows for today (ready + watching).
The dashboard API response only exposes a subset of meta fields
(gap_pct), so the Discord message shows ticker + gap + catalyst, but
not entry/stop. Once the proper scheduled job is deployed it will
include entry/stop too (those live in DB meta and are read directly).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

import requests

# Make `core` importable when running from project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.discord import format_candidate_summary, make_discord_notifier
from core.news import fetch_headline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("oneshot_discord_via_dashboard")

DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://dashboard-blond-iota-80.vercel.app",
).rstrip("/")
JOB_TIMEOUT_SEC = 60.0
HTTP_TIMEOUT_SEC = 10.0


def _fetch_watchlist() -> dict:
    url = f"{DASHBOARD_URL}/api/watchlist"
    logger.info("Fetching %s", url)
    resp = requests.get(url, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.json()


def _filter_today_ep(payload: dict) -> list[dict]:
    """Return ready + watching EP earnings/news rows.

    Includes yesterday's watching rows because those are C-pending
    candidates awaiting today's 3:35 PM day-2 confirm (so they're
    forward-looking from today's perspective). The dashboard API only
    surfaces non-expired rows, so no scan_date floor needed here.
    """
    out: list[dict] = []
    for stage in ("ready", "watching"):
        for row in payload.get(stage, []):
            if row.get("setup_raw") not in ("ep_earnings", "ep_news"):
                continue
            out.append(
                {
                    "ticker": row["ticker"],
                    "setup_type": row["setup_raw"],
                    "stage": stage,
                    "meta": {
                        "gap_pct": row.get("gap_pct"),
                    },
                }
            )
    return out


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL env var is not set", file=sys.stderr)
        return 2

    finnhub_key = os.environ.get("FINNHUB_API_KEY", "").strip() or None
    notify_discord = make_discord_notifier(webhook_url)

    try:
        payload = _fetch_watchlist()
    except (requests.RequestException, ValueError) as e:
        print(f"ERROR: failed to fetch dashboard watchlist: {e}", file=sys.stderr)
        return 1

    candidates = _filter_today_ep(payload)
    today_iso = datetime.now().strftime("%Y-%m-%d")
    logger.info(
        "Found %d EP candidate(s) for %s (ready+watching)",
        len(candidates),
        today_iso,
    )

    if not candidates:
        notify_discord(format_candidate_summary([]))
        return 0

    started = time.monotonic()
    enriched: list[dict] = []
    for c in candidates:
        if time.monotonic() - started > JOB_TIMEOUT_SEC:
            logger.warning(
                "60s wall-clock cap hit at %s; remaining tickers will get no headline",
                c["ticker"],
            )
            c["headline"] = None
        else:
            c["headline"] = fetch_headline(c["ticker"], finnhub_key=finnhub_key)
        enriched.append(c)

    msg = format_candidate_summary(enriched)
    logger.info("Posting %d-char message to Discord", len(msg))
    print(msg, file=sys.stderr)
    notify_discord(msg)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

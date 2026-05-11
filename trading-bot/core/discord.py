"""Discord webhook transport + Discord-only message formatting.

The Discord notifier is intentionally separate from the Telegram
notifier in main.py: only the daily candidate summary fans out to
Discord, and the formatting differs (no A/B/C breakdown, no strategy
tag after ticker, with a catalyst headline line per ticker).

If `webhook_url` is unset, `make_discord_notifier` returns a no-op
that simply logs at INFO level. Safe default for local dev and for
the period before the operator drops a real URL into the config.

Network calls use hard 5-second timeouts. A webhook outage logs a
warning but never raises into the caller — Discord is best-effort
and must not be allowed to disrupt anything trade-related.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SEC = 5.0
DISCORD_MAX_CONTENT = 1900  # Hard limit is 2000; leave headroom.


def _split_for_discord(message: str, limit: int = DISCORD_MAX_CONTENT) -> list[str]:
    """Split `message` into <= `limit`-char chunks at line boundaries.

    Greedy pack lines into a chunk; flush when adding the next line
    would exceed `limit`. A single line longer than `limit` is hard-cut.
    """
    if len(message) <= limit:
        return [message] if message else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in message.split("\n"):
        # +1 for the joining "\n"
        added = len(line) + (1 if current else 0)
        if current_len + added > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            if len(line) > limit:
                # Hard-cut a single oversized line
                while line:
                    chunks.append(line[:limit])
                    line = line[limit:]
                continue
        current.append(line)
        current_len += added
    if current:
        chunks.append("\n".join(current))
    return chunks


def make_discord_notifier(webhook_url: Optional[str]) -> Callable[[str], None]:
    """Return a callable that POSTs `message` to the Discord webhook.

    Long messages are split at line boundaries and posted as multiple
    sequential webhook calls so the user sees the full content rather
    than a 1900-char truncation. Returns a no-op stub when webhook_url
    is empty/None so callers never need to check whether Discord is
    configured.
    """
    if not webhook_url:
        return lambda msg: logger.info("[Discord stub] %s", msg)

    def notify(message: str) -> None:
        if not message:
            return
        chunks = _split_for_discord(message)
        for idx, chunk in enumerate(chunks):
            try:
                resp = requests.post(
                    webhook_url,
                    json={"content": chunk},
                    timeout=WEBHOOK_TIMEOUT_SEC,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "Discord webhook returned %s for chunk %d/%d: %s",
                        resp.status_code,
                        idx + 1,
                        len(chunks),
                        resp.text[:200],
                    )
            except requests.RequestException as e:
                logger.warning(
                    "Discord webhook send failed for chunk %d/%d: %s",
                    idx + 1,
                    len(chunks),
                    e,
                )

    return notify


def format_candidate_summary(candidates: list[dict]) -> str:
    """Format EP earnings + EP news candidates for the Discord post.

    Per spec: no A/B/C bucket counts, no "(A)" tag after ticker, one
    catalyst headline line per ticker with a graceful fallback when
    both news sources strike out.

    Each candidate dict should have:
      - ticker (str)
      - setup_type ("ep_earnings" | "ep_news")
      - stage ("ready" | "watching")
      - meta (dict — gap_pct, entry_price, stop_price, gap_day_close, ...)
      - headline (dict | None — {"title", "url"})
    """
    if not candidates:
        return "No EP candidates today."

    by_setup: dict[str, list[dict]] = {}
    for c in candidates:
        by_setup.setdefault(c["setup_type"], []).append(c)

    lines: list[str] = ["**EP CANDIDATES TODAY**", ""]

    setup_label = {"ep_earnings": "EP Earnings", "ep_news": "EP News"}
    for setup in ("ep_earnings", "ep_news"):
        items = by_setup.get(setup, [])
        if not items:
            continue
        lines.append(f"__{setup_label[setup]} ({len(items)})__")
        for c in items:
            lines.extend(_format_one(c))
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_one(c: dict) -> list[str]:
    ticker = c["ticker"]
    stage = c.get("stage", "")
    meta = c.get("meta") or {}
    gap = meta.get("gap_pct")

    gap_str = f"gap {gap:.1f}%" if isinstance(gap, (int, float)) else "gap n/a"

    if stage == "ready":
        entry = meta.get("entry_price")
        stop = meta.get("stop_price")
        head = f"  **{ticker}**  {gap_str}"
        if isinstance(entry, (int, float)) and isinstance(stop, (int, float)):
            head += f"  entry ${entry:.2f}  stop ${stop:.2f}"
    else:
        # watching: C-pending, no entry/stop yet
        gap_close = meta.get("gap_day_close")
        head = f"  **{ticker}**  {gap_str}  pending day-2 confirm"
        if isinstance(gap_close, (int, float)):
            head += f"  (gap close ${gap_close:.2f})"

    headline = c.get("headline")
    if headline and headline.get("title"):
        catalyst = f"    catalyst: {headline['title']}"
    else:
        catalyst = "    catalyst: (no headline available)"

    return [head, catalyst]

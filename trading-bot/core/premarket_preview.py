"""Pre-market preview: 8:30 AM ET scan of gap-up tickers for Discord.

Informational only. Posts a one-shot Discord summary listing the top
gappers visible in pre-market trade, classified as earnings vs news,
with a catalyst headline per ticker. Never writes to Watchlist, never
touches the trade path. Same isolation guarantees as
`job_discord_candidate_summary` (3:10 PM).

Why pre-market is different from the 3:00 PM scan:
  - Alpaca's `daily_bar.open` is None until 9:30 AM ET, so the
    standard scanner gap formula (`open - prev_close`) doesn't work.
  - We substitute `latest_trade.price - prev_close` and skip the
    "open > prev_high" / "open > 200d SMA" filters that require the
    regular-session open price.
  - RVOL is omitted; pre-market volume is too thin to be informative
    for universe-wide ranking.

The scan filter cascade is deliberately loose (gap >= 5%) so the
operator sees a slightly broader set than what the 3:00 PM A/B
strategy will actually trade.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pytz
import yfinance as yf

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


# ---------------------------------------------------------------------------
# Phase 1: universe-wide gap scan
# ---------------------------------------------------------------------------

def scan_premarket_gappers(
    client,
    min_gap_pct: float = 5.0,
    min_prev_close: float = 3.0,
    pre_filter_top_n: int = 30,
) -> list[dict]:
    """Universe-wide pre-market gap scan via Alpaca snapshots.

    Returns up to `pre_filter_top_n` candidates sorted by gap%
    descending. Each candidate: {ticker, gap_pct, premarket_price,
    prev_close}. Caller is responsible for the market-cap filter,
    catalyst classification, and headline lookup (those are per-ticker
    yfinance calls and we only want to pay for the top N).
    """
    tickers = client.get_tradable_universe()
    snapshots = client.get_snapshots(tickers)

    candidates: list[dict] = []
    for sym, snap in snapshots.items():
        prev_close = snap.get("prev_close", 0) or 0
        latest_price = snap.get("latest_price", 0) or 0
        if prev_close < min_prev_close:
            continue
        if latest_price <= 0 or latest_price <= prev_close:
            continue
        gap_pct = (latest_price - prev_close) / prev_close * 100.0
        if gap_pct < min_gap_pct:
            continue
        candidates.append(
            {
                "ticker": sym,
                "gap_pct": gap_pct,
                "premarket_price": latest_price,
                "prev_close": prev_close,
            }
        )

    candidates.sort(key=lambda c: c["gap_pct"], reverse=True)
    return candidates[:pre_filter_top_n]


# ---------------------------------------------------------------------------
# Phase 2: yfinance enrichment (market cap + earnings classification)
# ---------------------------------------------------------------------------

def _get_ticker_info(ticker: str) -> tuple[float, str]:
    """Return (market_cap, quote_type) from yfinance.

    Mirrors `strategies/ep_earnings/scanner._get_ticker_info` so the
    "earnings vs news" classification stays consistent with what the
    3:00 PM trading scan would treat as earnings.
    """
    info = yf.Ticker(ticker).info
    return (
        float(info.get("marketCap", 0) or 0),
        str(info.get("quoteType", "") or ""),
    )


def _check_earnings_recent(ticker: str, today: date) -> bool:
    """True if the ticker reported earnings today or yesterday.

    Same logic as `strategies/ep_earnings/scanner._check_earnings_today`.
    """
    dates = yf.Ticker(ticker).get_earnings_dates(limit=4)
    if dates is None or (hasattr(dates, "empty") and dates.empty):
        return False
    yesterday = today - timedelta(days=1)
    for dt in dates.index:
        d = dt.date() if hasattr(dt, "date") else dt
        if d in (today, yesterday):
            return True
    return False


def enrich_candidates(
    candidates: list[dict],
    min_mcap: float = 800_000_000,
    target_count: int = 10,
) -> list[dict]:
    """Add market_cap + catalyst_type per candidate, filter mcap, cap at target.

    Per-ticker yfinance failures are tolerated (skip the candidate and
    log a warning). This is the partial-failure-in-batch carve-out
    permitted by CLAUDE.md — one bad ticker should not abort the
    informational pre-market post. yfinance fundamentals can be flaky
    on individual symbols (e.g. delisted, brand-new IPO).
    """
    today = datetime.now(ET).date()
    enriched: list[dict] = []

    for c in candidates:
        if len(enriched) >= target_count:
            break
        ticker = c["ticker"]

        try:
            mcap, qtype = _get_ticker_info(ticker)
        except Exception as e:
            logger.warning("yfinance info fail for %s: %s", ticker, e)
            continue

        if mcap < min_mcap:
            continue
        # Only equities; skip ETFs/MUTUALFUND/CURRENCY/etc.
        if qtype.upper() and qtype.upper() != "EQUITY":
            logger.debug("%s skipped: quoteType=%s", ticker, qtype)
            continue

        try:
            is_earnings = _check_earnings_recent(ticker, today)
        except Exception as e:
            logger.warning("earnings check fail for %s: %s", ticker, e)
            is_earnings = False

        c["market_cap"] = mcap
        c["catalyst_type"] = "earnings" if is_earnings else "news"
        enriched.append(c)

    return enriched


# ---------------------------------------------------------------------------
# Phase 3: Discord message formatting
# ---------------------------------------------------------------------------

def format_premarket_preview(candidates: list[dict]) -> str:
    """Format Discord post: header + grouped earnings/news sections.

    Each candidate should have ticker, gap_pct, premarket_price,
    prev_close, catalyst_type, and optionally a headline dict
    {"title", "url"} populated by the caller.
    """
    et_now = datetime.now(ET)
    timestamp = et_now.strftime("%I:%M %p ET, %Y-%m-%d")

    if not candidates:
        return (
            f"**PRE-MARKET PREVIEW** — {timestamp}\n"
            f"No qualifying gappers today (gap >= 5%, mcap >= $800M)."
        )

    lines: list[str] = [
        f"**PRE-MARKET PREVIEW** — {timestamp}",
        f"Top {len(candidates)} gappers, gap >= 5%, mcap >= $800M",
        "",
    ]

    earnings = [c for c in candidates if c.get("catalyst_type") == "earnings"]
    news = [c for c in candidates if c.get("catalyst_type") == "news"]

    if earnings:
        lines.append(f"__EARNINGS ({len(earnings)})__")
        for c in earnings:
            lines.extend(_format_one(c))
        lines.append("")
    if news:
        lines.append(f"__NEWS ({len(news)})__")
        for c in news:
            lines.extend(_format_one(c))

    return "\n".join(lines).rstrip()


def _format_one(c: dict) -> list[str]:
    ticker = c["ticker"]
    gap = c.get("gap_pct", 0)
    price = c.get("premarket_price")
    prev = c.get("prev_close")
    headline = c.get("headline")

    if isinstance(price, (int, float)) and isinstance(prev, (int, float)):
        head = f"  **{ticker}**  +{gap:.1f}%  ${price:.2f} (prev ${prev:.2f})"
    else:
        head = f"  **{ticker}**  +{gap:.1f}%"

    if headline and headline.get("title"):
        catalyst = f"    {headline['title']}"
    else:
        catalyst = "    (no headline available)"

    return [head, catalyst]

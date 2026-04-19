"""Shared constants for API routes."""

# Pipeline schedule — rich definition of all daily jobs, ordered by execution time.
# display_day_offset=1 means the job visually belongs to the *next* trading day's pipeline.
PIPELINE_SCHEDULE = [
    {
        "job_id": "breakout_nightly_scan",
        "label": "Nightly Breakout Scan",
        "time": "17:00",
        "category": "scan",
        "phase": "overnight",
        "description": "Scans all US equities for breakout consolidation patterns. Feeds tomorrow's watchlist.",
        "display_day_offset": 1,
    },
    {
        "job_id": "premarket_scan",
        "label": "Pre-market Scan",
        "time": "06:00",
        "category": "scan",
        "phase": "premarket",
        "description": "Re-scans watchlist candidates with fresh pre-market price and volume data.",
        "display_day_offset": 0,
    },
    {
        "job_id": "subscribe_watchlist",
        "label": "Subscribe Watchlist",
        "time": "09:25",
        "category": "system",
        "phase": "premarket",
        "description": "Subscribes to real-time quotes for all active watchlist tickers before the bell.",
        "display_day_offset": 0,
    },
    {
        "job_id": "intraday_monitor",
        "label": "Intraday Monitor",
        "time": "09:30",
        "category": "monitor",
        "phase": "market_open",
        "description": "Activates the live trading stream. Evaluates entry signals on each 1-minute bar.",
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_earnings_scan",
        "label": "EP Earnings Scan",
        "time": "15:00",
        "category": "scan",
        "phase": "afternoon",
        "description": "Scans for earnings gap-up stocks meeting EP Strategy A/B entry filters.",
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_news_scan",
        "label": "EP News Scan",
        "time": "15:05",
        "category": "scan",
        "phase": "afternoon",
        "description": "Scans for news-driven gap-up stocks meeting EP swing entry filters.",
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_earnings_execute",
        "label": "EP Earnings Execute",
        "time": "15:50",
        "category": "trade",
        "phase": "afternoon",
        "description": "Places limit orders for approved EP earnings swing setups near the close.",
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_news_execute",
        "label": "EP News Execute",
        "time": "15:50",
        "category": "trade",
        "phase": "afternoon",
        "description": "Places limit orders for approved EP news swing setups near the close.",
        "display_day_offset": 0,
    },
    {
        "job_id": "eod_tasks",
        "label": "End-of-Day Tasks",
        "time": "15:55",
        "category": "system",
        "phase": "close",
        "description": "Records daily P&L, expires stale watchlist entries, sends Telegram summary.",
        "display_day_offset": 0,
    },
]

JOB_LABELS = {job["job_id"]: job["label"] for job in PIPELINE_SCHEDULE}
# Add recurring jobs that aren't in the pipeline timeline
JOB_LABELS["reconcile_positions"] = "Reconcile positions"
JOB_LABELS["heartbeat"] = "Heartbeat"

# Phase metadata for UI section headers
PHASE_META = {
    "overnight": {"label": "Overnight", "time_range": "5:00 PM"},
    "premarket": {"label": "Pre-Market", "time_range": "6:00 \u2013 9:25 AM"},
    "market_open": {"label": "Market Open", "time_range": "9:30 AM"},
    "afternoon": {"label": "Afternoon Swing", "time_range": "3:00 \u2013 3:50 PM"},
    "close": {"label": "Close", "time_range": "3:55 PM"},
}

# Ordered list of phases for consistent display
PHASE_ORDER = ["overnight", "premarket", "market_open", "afternoon", "close"]

# ── Strategy ↔ job mapping ───────────────────────────────────��──────

# Per-job ownership:
#   frozenset({slug}):          strategy-owned, shows under that strategy's tab
#   frozenset({slug1, slug2}):  multi-owner, shows under "Shared" if any owner enabled
#   None:                       always-on, shows under "Shared" unconditionally
JOB_OWNERS: dict[str, frozenset[str] | None] = {
    "breakout_nightly_scan": frozenset({"breakout"}),
    "premarket_scan": frozenset({"breakout", "episodic_pivot"}),
    "subscribe_watchlist": frozenset({"breakout", "episodic_pivot"}),
    "intraday_monitor": None,
    "ep_earnings_scan": frozenset({"ep_earnings"}),
    "ep_news_scan": frozenset({"ep_news"}),
    "ep_earnings_execute": frozenset({"ep_earnings"}),
    "ep_news_execute": frozenset({"ep_news"}),
    "ep_earnings_day2_confirm": frozenset({"ep_earnings"}),
    "ep_news_day2_confirm": frozenset({"ep_news"}),
    "eod_tasks": None,
    "reconcile_positions": None,
    "heartbeat": None,
}

# All known strategies with display names and descriptions
STRATEGY_META = {
    "ep_earnings": {
        "display_name": "EP Earnings Swing",
        "description": "Long swing setup on earnings-driven gap-ups. Evaluates Strategy A (tight), B (relaxed), and C (day-2 confirmation).",
    },
    "ep_news": {
        "display_name": "EP News Swing",
        "description": "Long swing setup on news-driven gap-ups (non-earnings). Uses the same A/B/C framework with news catalysts.",
    },
    "breakout": {
        "display_name": "Breakout",
        "description": "Consolidation breakout setup. Scans for stocks in tight ranges with rising momentum, enters on opening-range breakout.",
    },
    "episodic_pivot": {
        "display_name": "Episodic Pivot",
        "description": "Intraday long on gap-up stocks triggered by catalysts (earnings, news). Enters on opening-range high breakout.",
    },
    "parabolic_short": {
        "display_name": "Parabolic Short",
        "description": "Short setup on overextended stocks. Disabled — negative expectancy in backtests.",
    },
}

# Extra job_ids that are in plugins but not in PIPELINE_SCHEDULE
STRATEGY_EXTRA_JOBS = {
    "ep_earnings": ["ep_earnings_day2_confirm"],
    "ep_news": ["ep_news_day2_confirm"],
}


def job_owners(job_id: str) -> frozenset[str] | None:
    """Return the set of strategies that own/need a job, or None if always-on.

    Falls back to prefix-match against STRATEGY_META for unregistered job_ids.
    """
    if job_id in JOB_OWNERS:
        return JOB_OWNERS[job_id]
    for slug in STRATEGY_META:
        if job_id.startswith(slug):
            return frozenset({slug})
    return None


def job_to_strategy(job_id: str) -> str | None:
    """Return the single owning strategy slug, or None for shared/always-on jobs.

    Multi-owner jobs (e.g. premarket_scan, which serves breakout + episodic_pivot)
    return None — these belong under the "Shared" tab.
    """
    owners = job_owners(job_id)
    if owners is None or len(owners) != 1:
        return None
    return next(iter(owners))


def is_job_active(job_id: str, enabled_slugs) -> bool:
    """Return True if the job should be scheduled/displayed given enabled strategies."""
    owners = job_owners(job_id)
    if owners is None:
        return True
    return bool(owners & set(enabled_slugs))

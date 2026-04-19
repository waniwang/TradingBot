"""Shared constants for API routes."""

# Pipeline schedule — rich definition of all daily jobs, ordered by execution time.
# display_day_offset=1 means the job visually belongs to the *next* trading day's pipeline.
# end_time is set for jobs that run as a window (long-running monitor, retry loops);
# it is displayed on the timeline as "start – end". Omit for point-in-time jobs.
PIPELINE_SCHEDULE = [
    {
        "job_id": "breakout_nightly_scan",
        "label": "Nightly Breakout Scan",
        "time": "17:00",
        "category": "scan",
        "phase": "overnight",
        "description": (
            "Ranks every US equity on relative strength and consolidation tightness. "
            "Feeds tomorrow's breakout watchlist and takes ~15 minutes on a batch of ~1,500 tickers."
        ),
        "display_day_offset": 1,
    },
    {
        "job_id": "premarket_scan",
        "label": "Pre-market Scan",
        "time": "06:00",
        "category": "scan",
        "phase": "premarket",
        "description": (
            "Refreshes daily bars for all watchlist tickers and gathers pre-market gap/RVOL. "
            "Promotes breakout candidates to active and seeds the Episodic Pivot gap list."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "subscribe_watchlist",
        "label": "Subscribe Watchlist",
        "time": "09:25",
        "category": "system",
        "phase": "premarket",
        "description": (
            "Opens the Alpaca 1-minute-bar WebSocket stream for every active watchlist ticker. "
            "Required before any intraday signal can fire."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "intraday_monitor",
        "label": "Intraday Monitor",
        "time": "09:30",
        "end_time": "16:00",
        "category": "monitor",
        "phase": "market_open",
        "description": (
            "Drives live trading from 9:30 AM to 4:00 PM ET. On every 1-minute bar: evaluates "
            "entry signals (ORH/ORB), checks stops, takes a 40% partial exit at +15%, updates "
            "trailing stops, and enforces the 10-day-MA trailing-close rule at the end of the day."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_earnings_scan",
        "label": "EP Earnings Scan",
        "time": "15:00",
        "category": "scan",
        "phase": "afternoon",
        "description": (
            "Scans earnings-driven gap-ups (>8% gap, prev close >$3, mcap >$800M, open above "
            "prev high + 200-day SMA, RVOL >1). Saves approved A/B candidates for 3:50 PM entry "
            "and parks Strategy C candidates for day-2 confirmation."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_news_scan",
        "label": "EP News Scan",
        "time": "15:05",
        "category": "scan",
        "phase": "afternoon",
        "description": (
            "Same filter stack as the earnings scan but for non-earnings catalysts (news-driven "
            "gaps). Saves approved A/B candidates for 3:50 PM entry and parks C for day-2 confirm."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_earnings_day2_confirm",
        "label": "EP Earnings Day-2 Confirm",
        "time": "15:45",
        "category": "scan",
        "phase": "afternoon",
        "description": (
            "Checks yesterday's Strategy C earnings candidates against today's price. Promotes "
            "to ready (for the 3:50 execute) if price > gap-day close; expires the row otherwise."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_news_day2_confirm",
        "label": "EP News Day-2 Confirm",
        "time": "15:45",
        "category": "scan",
        "phase": "afternoon",
        "description": (
            "Checks yesterday's Strategy C news candidates against today's price. Promotes to "
            "ready (for the 3:50 execute) if price > gap-day close; expires the row otherwise."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_earnings_execute",
        "label": "EP Earnings Execute",
        "time": "15:50",
        "end_time": "15:59",
        "category": "trade",
        "phase": "afternoon",
        "description": (
            "Places limit orders for every approved EP earnings candidate (A/B/C). Fires once "
            "per minute from 3:50 to 3:59 — each run is idempotent (skips tickers already "
            "traded today), so retries cover transient broker/network errors."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "ep_news_execute",
        "label": "EP News Execute",
        "time": "15:50",
        "end_time": "15:59",
        "category": "trade",
        "phase": "afternoon",
        "description": (
            "Places limit orders for every approved EP news candidate (A/B/C). Fires once per "
            "minute from 3:50 to 3:59 — each run is idempotent, so retries cover transient "
            "broker/network errors."
        ),
        "display_day_offset": 0,
    },
    {
        "job_id": "eod_tasks",
        "label": "End-of-Day Tasks",
        "time": "15:55",
        "category": "system",
        "phase": "close",
        "description": (
            "Applies max-hold-period exits (50 days for A/B, 20 for C) and the 10-day-MA "
            "trailing-close exit for positions past partial. Records daily P&L, expires stale "
            "watchlist rows, resets the daily-loss halt, and sends the Telegram summary."
        ),
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
    "market_open": {"label": "Market Open", "time_range": "9:30 AM \u2013 4:00 PM"},
    "afternoon": {"label": "Afternoon Swing", "time_range": "3:00 \u2013 3:59 PM"},
    "close": {"label": "Close", "time_range": "3:55 PM"},
}

# Ordered list of phases for consistent display
PHASE_ORDER = ["overnight", "premarket", "market_open", "afternoon", "close"]

# ── Strategy ↔ job mapping ─────────────────────────────────────────

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

# Extra job_ids that were historically registered by plugins but not in PIPELINE_SCHEDULE.
# day2_confirm jobs have since moved into PIPELINE_SCHEDULE, so this map is now empty —
# kept to preserve the API surface for callers that still consult it.
STRATEGY_EXTRA_JOBS: dict[str, list[str]] = {}


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

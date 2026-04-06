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

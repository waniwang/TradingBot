"""Shared constants for API routes."""

# Pipeline schedule — static definition of all daily jobs, ordered by time.
# Each entry: (job_id, label, time_str HH:MM ET, category)
PIPELINE_SCHEDULE = [
    ("breakout_nightly_scan", "Nightly Breakout Scan", "17:00", "scan"),
    ("premarket_scan", "Pre-market Scan", "06:00", "scan"),
    ("subscribe_watchlist", "Subscribe Watchlist", "09:25", "system"),
    ("intraday_monitor", "Intraday Monitor", "09:30", "monitor"),
    ("ep_earnings_scan", "EP Earnings Scan", "15:00", "scan"),
    ("ep_news_scan", "EP News Scan", "15:05", "scan"),
    ("ep_earnings_execute", "EP Earnings Execute", "15:50", "trade"),
    ("ep_news_execute", "EP News Execute", "15:50", "trade"),
    ("eod_tasks", "End-of-Day Tasks", "15:55", "system"),
]

JOB_LABELS = {job_id: label for job_id, label, _, _ in PIPELINE_SCHEDULE}
# Add recurring jobs that aren't in the pipeline timeline
JOB_LABELS["reconcile_positions"] = "Reconcile positions"
JOB_LABELS["heartbeat"] = "Heartbeat"

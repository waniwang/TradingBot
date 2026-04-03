"""
Breakout nightly scanner.

Orchestrates the full nightly watchlist update:
1. Fetch tradable universe
2. Rank by momentum (top 100)
3. Analyze consolidation for each
4. Upsert into DB (new entries + update existing)
5. Age out stale watching entries

Re-exports from scanner/consolidation.py and scanner/momentum_rank.py
since these contain the core analysis logic shared between nightly scan
and breakout signal evaluation.
"""

from __future__ import annotations

import logging
from typing import Any

from scanner.watchlist_manager import run_nightly_scan

logger = logging.getLogger(__name__)


def nightly_scan_job(config: dict, client, db_engine, notify) -> dict[str, Any]:
    """
    Called by the scheduler at 5:00 PM ET.
    Delegates to the existing run_nightly_scan in watchlist_manager.
    """
    def _progress(task="", detail=""):
        if task and notify:
            msg = f"Breakout nightly: {task}"
            if detail:
                msg += f" ({detail})"
            logger.info(msg)

    summary = run_nightly_scan(config, client, db_engine, progress_cb=_progress)

    if notify and "error" not in summary:
        notify(
            f"BREAKOUT NIGHTLY SCAN DONE\n"
            f"Universe: {summary.get('universe_raw', '?')} → "
            f"Top momentum: {summary.get('momentum_top', '?')}\n"
            f"New: {summary.get('new', 0)}, Updated: {summary.get('updated', 0)}, "
            f"Ready: {summary.get('ready', 0)}, Watching: {summary.get('watching', 0)}, "
            f"Aged out: {summary.get('aged_out', 0)}"
        )
    elif notify:
        notify(f"BREAKOUT NIGHTLY SCAN ERROR: {summary.get('error')}")

    return summary

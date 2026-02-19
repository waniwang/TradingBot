"""
Unit tests for main module helpers.
"""

import pytest
from main import _format_watchlist_notification


# ---------------------------------------------------------------------------
# _format_watchlist_notification
# ---------------------------------------------------------------------------

class TestFormatWatchlistNotification:
    def test_empty_watchlist(self):
        result = _format_watchlist_notification([])
        assert result == "WATCHLIST READY: 0 candidates"

    def test_single_ep_candidate(self):
        watchlist = [
            {"ticker": "NVDA", "setup_type": "episodic_pivot", "gap_pct": 15.2},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 1 candidates" in result
        assert "EP: NVDA (+15.2%)" in result

    def test_single_breakout_candidate(self):
        watchlist = [
            {"ticker": "MSFT", "setup_type": "breakout", "atr_ratio": 0.75},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 1 candidates" in result
        assert "Breakout: MSFT" in result

    def test_mixed_setup_types(self):
        watchlist = [
            {"ticker": "NVDA", "setup_type": "episodic_pivot", "gap_pct": 15.2},
            {"ticker": "AAPL", "setup_type": "episodic_pivot", "gap_pct": 12.1},
            {"ticker": "MSFT", "setup_type": "breakout", "atr_ratio": 0.75},
            {"ticker": "TSLA", "setup_type": "breakout", "atr_ratio": 0.80},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "WATCHLIST READY: 4 candidates" in result
        assert "EP: NVDA (+15.2%), AAPL (+12.1%)" in result
        assert "Breakout: MSFT, TSLA" in result

    def test_breakout_no_gap_pct(self):
        """Breakout candidates typically have no gap_pct — show ticker only."""
        watchlist = [
            {"ticker": "AMZN", "setup_type": "breakout"},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "Breakout: AMZN" in result
        # No percentage should appear for breakout
        assert "%" not in result.split("Breakout:")[1]

    def test_parabolic_short_label(self):
        watchlist = [
            {"ticker": "MEME", "setup_type": "parabolic_short", "gap_pct": 0},
        ]
        result = _format_watchlist_notification(watchlist)
        assert "Parabolic Short: MEME" in result

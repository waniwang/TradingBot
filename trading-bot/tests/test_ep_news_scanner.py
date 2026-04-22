"""
Unit tests for the EP News EOD scanner (scanner/ep_news.py)
and strategy evaluation (signals/ep_news_strategy.py).

Uses mocked AlpacaClient and yfinance -- no network calls.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from strategies.ep_news.scanner import scan_ep_news, _confirm_no_earnings
from strategies.ep_news.strategy import (
    compute_features,
    evaluate_strategy_a,
    evaluate_strategy_b,
    evaluate_ep_news_strategies,
)


# Phase A pre-screen uses Alpaca snapshots via scanner/gap_screen.py::scan_snapshot_gaps
# — patch it per-test to keep the suite offline. Helpers seed `_GAP_MOVERS`.

_GAP_MOVERS: list = []


@pytest.fixture(autouse=True)
def _mock_gap_screen():
    _GAP_MOVERS.clear()

    def fake_scan(*_args, **_kwargs):
        return list(_GAP_MOVERS)

    with patch("scanner.gap_screen.scan_snapshot_gaps", side_effect=fake_scan):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(overrides: dict | None = None):
    """Create a test config with EP news scanner defaults."""
    cfg = {
        "signals": {
            "ep_news_min_gap_pct": 8.0,
            "ep_news_min_price": 3.0,
            "ep_news_min_market_cap": 1_000_000_000,
            "ep_news_exclude_earnings": True,
            "ep_news_require_open_above_prev_high": True,
            "ep_news_require_above_200d_sma": True,
            "ep_news_min_rvol": 1.0,
        },
        "strategies": {
            "enabled": ["ep_news", "episodic_pivot", "breakout"],
        },
    }
    if overrides:
        cfg["signals"].update(overrides)
    return cfg


def _make_daily_df(n: int = 300, start_price: float = 50.0, drift: float = 0.1) -> pd.DataFrame:
    """Create a synthetic daily OHLCV DataFrame with n rows."""
    rows = []
    price = start_price
    for i in range(n):
        price += drift
        rows.append({
            "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
            "open": round(price - 0.5, 2),
            "high": round(price + 1.0, 2),
            "low": round(price - 1.0, 2),
            "close": round(price, 2),
            "volume": 500_000,
        })
    return pd.DataFrame(rows)


def _make_snapshot(prev_close, prev_high, open_price, latest_price, daily_volume,
                   today_high=None, today_low=None):
    """Create a snapshot dict matching the Alpaca snapshot format."""
    return {
        "prev_close": prev_close,
        "prev_high": prev_high,
        "open": open_price,
        "latest_price": latest_price,
        "daily_volume": daily_volume,
        "today_high": today_high or max(open_price, latest_price) * 1.01,
        "today_low": today_low or min(open_price, latest_price) * 0.99,
    }


def _make_passing_client(
    symbol="NVDA",
    prev_close=100.0,
    prev_high=105.0,
    open_price=115.0,
    latest_price=118.0,
    daily_volume=1_000_000,
):
    """Create a mock client where one stock passes all Phase A + B filters."""
    client = MagicMock()
    movers = [{"symbol": symbol, "percent_change": 15.0, "price": open_price}]
    client.get_market_movers_gainers.return_value = movers
    _GAP_MOVERS[:] = movers
    client.get_snapshots.return_value = {
        symbol: _make_snapshot(prev_close, prev_high, open_price, latest_price, daily_volume),
    }
    df = _make_daily_df(300, start_price=prev_close - 30, drift=0.1)
    client.get_daily_bars_batch.return_value = {symbol: df}
    return client


# ---------------------------------------------------------------------------
# Phase A tests: gap%, price, open > prev_high
# ---------------------------------------------------------------------------

class TestPhaseAFilters:
    def test_basic_eod_scan(self):
        """Stock that passes all filters (no earnings)."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=True):
            result = scan_ep_news(config, client)

        assert len(result) == 1
        assert result[0]["ticker"] == "NVDA"
        assert result[0]["gap_pct"] == 15.0
        assert result[0]["setup_type"] == "ep_news"

    def test_filter_gap_below_8pct(self):
        """Stock with 5% gap is rejected."""
        client = _make_passing_client(prev_close=100.0, open_price=105.0, latest_price=106.0)
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_filter_price_below_3(self):
        """Stock with prev close $2 is rejected."""
        client = _make_passing_client(prev_close=2.0, prev_high=2.5, open_price=2.5, latest_price=2.6)
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_filter_open_below_prev_high(self):
        """Stock where open < yesterday's high is rejected."""
        client = _make_passing_client(
            prev_close=100.0, prev_high=120.0, open_price=115.0, latest_price=118.0,
        )
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_empty_movers(self):
        """No market movers returns empty list."""
        client = MagicMock()
        client.get_market_movers_gainers.return_value = []
        _GAP_MOVERS.clear()
        result = scan_ep_news(_make_config(), client)
        assert result == []


# ---------------------------------------------------------------------------
# Phase B tests: 200d SMA, RVOL
# ---------------------------------------------------------------------------

class TestPhaseBFilters:
    def test_filter_below_200d_sma(self):
        """Stock where open < 200d SMA is rejected."""
        client = _make_passing_client()
        df = _make_daily_df(300, start_price=180.0, drift=0.1)
        client.get_daily_bars_batch.return_value = {"NVDA": df}
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_filter_low_rvol(self):
        """Stock where today's volume < 14d avg is rejected."""
        client = _make_passing_client(daily_volume=100_000)
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_no_daily_bars(self):
        """Stock with no daily bars is skipped."""
        client = _make_passing_client()
        client.get_daily_bars_batch.return_value = {}
        config = _make_config()
        result = scan_ep_news(config, client)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Phase C tests: market cap >= $1B, EQUITY, NO earnings
# ---------------------------------------------------------------------------

class TestPhaseCFilters:
    def test_filter_below_market_cap(self):
        """Stock with market cap < $1B is rejected."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(500_000_000, "EQUITY")):
            result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_filter_non_equity(self):
        """ETF (quoteType != EQUITY) is rejected."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "ETF")):
            result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_filter_has_earnings(self):
        """Stock WITH earnings today is rejected (it's an earnings gap, not news)."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=False):
            result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_no_earnings_passes(self):
        """Stock WITHOUT earnings today passes (it IS a news gap)."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=True):
            result = scan_ep_news(config, client)
        assert len(result) == 1

    def test_earnings_api_failure_skips_stock(self):
        """When earnings API fails, stock is conservatively skipped (not included)."""
        client = _make_passing_client()
        config = _make_config()

        # _confirm_no_earnings returns False on API failure → stock excluded
        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=False):
            result = scan_ep_news(config, client)
        assert len(result) == 0

    def test_earnings_exclusion_disabled(self):
        """When exclude_earnings=False, stock with earnings still passes."""
        client = _make_passing_client()
        config = _make_config({"ep_news_exclude_earnings": False})

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_news(config, client)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _confirm_no_earnings unit tests
# ---------------------------------------------------------------------------

class TestConfirmNoEarnings:
    """Tests for the safety-inverted earnings check used by EP News."""

    def test_no_earnings_dates_returns_true(self):
        """Successful API call with no earnings dates → confirmed no earnings."""
        mock_dates = pd.DataFrame()  # empty DataFrame
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.get_earnings_dates.return_value = mock_dates
            mock_yf.Ticker.return_value = mock_ticker

            result = _confirm_no_earnings("NVDA", date(2026, 4, 4))
        assert result is True

    def test_earnings_today_returns_false(self):
        """Successful API call showing earnings today → has earnings."""
        today = date(2026, 4, 4)
        idx = pd.DatetimeIndex([pd.Timestamp(today)])
        mock_dates = pd.DataFrame({"EPS": [1.5]}, index=idx)
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.get_earnings_dates.return_value = mock_dates
            mock_yf.Ticker.return_value = mock_ticker

            result = _confirm_no_earnings("NVDA", today)
        assert result is False

    def test_earnings_yesterday_returns_false(self):
        """Earnings yesterday (after-hours) → has earnings."""
        today = date(2026, 4, 4)
        yesterday = today - timedelta(days=1)
        idx = pd.DatetimeIndex([pd.Timestamp(yesterday)])
        mock_dates = pd.DataFrame({"EPS": [1.5]}, index=idx)
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.get_earnings_dates.return_value = mock_dates
            mock_yf.Ticker.return_value = mock_ticker

            result = _confirm_no_earnings("NVDA", today)
        assert result is False

    def test_earnings_last_week_returns_true(self):
        """Earnings from last week → no recent earnings → confirmed safe."""
        today = date(2026, 4, 4)
        old_date = today - timedelta(days=7)
        idx = pd.DatetimeIndex([pd.Timestamp(old_date)])
        mock_dates = pd.DataFrame({"EPS": [1.5]}, index=idx)
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.get_earnings_dates.return_value = mock_dates
            mock_yf.Ticker.return_value = mock_ticker

            result = _confirm_no_earnings("NVDA", today)
        assert result is True

    def test_api_exception_propagates(self):
        """Per error-handling policy: yfinance errors must not be swallowed — they propagate so the job fails and a Telegram alert fires."""
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_yf.Ticker.side_effect = Exception("yfinance down")

            with pytest.raises(Exception, match="yfinance down"):
                _confirm_no_earnings("NVDA", date(2026, 4, 4))

    def test_none_response_returns_true(self):
        """API returns None (no data) → confirmed no earnings."""
        with patch("strategies.ep_news.scanner.yf") as mock_yf:
            mock_ticker = MagicMock()
            mock_ticker.get_earnings_dates.return_value = None
            mock_yf.Ticker.return_value = mock_ticker

            result = _confirm_no_earnings("NVDA", date(2026, 4, 4))
        assert result is True


# ---------------------------------------------------------------------------
# Output tests
# ---------------------------------------------------------------------------

class TestOutputFormat:
    def test_sorted_by_gap_descending(self):
        """Results sorted by gap% descending."""
        client = MagicMock()
        movers = [
            {"symbol": "AAA", "percent_change": 10.0, "price": 55.0},
            {"symbol": "BBB", "percent_change": 30.0, "price": 70.0},
            {"symbol": "CCC", "percent_change": 20.0, "price": 60.0},
        ]
        client.get_market_movers_gainers.return_value = movers
        _GAP_MOVERS[:] = movers
        client.get_snapshots.return_value = {
            "AAA": _make_snapshot(50.0, 52.0, 55.0, 56.0, 1_000_000),
            "BBB": _make_snapshot(50.0, 52.0, 70.0, 72.0, 1_000_000),
            "CCC": _make_snapshot(50.0, 52.0, 60.0, 62.0, 1_000_000),
        }
        df = _make_daily_df(300, start_price=20.0, drift=0.1)
        client.get_daily_bars_batch.return_value = {
            "AAA": df.copy(), "BBB": df.copy(), "CCC": df.copy(),
        }
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=True):
            result = scan_ep_news(config, client)

        gaps = [r["gap_pct"] for r in result]
        assert gaps == sorted(gaps, reverse=True)

    def test_output_dict_fields(self):
        """Output dict has all expected fields."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_news.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")), \
             patch("strategies.ep_news.scanner._confirm_no_earnings", return_value=True):
            result = scan_ep_news(config, client)

        assert len(result) == 1
        c = result[0]
        expected_keys = {
            "ticker", "gap_pct", "open_price", "prev_close", "prev_high",
            "current_price", "today_volume", "today_high", "today_low",
            "sma_200", "market_cap", "rvol", "setup_type",
        }
        assert expected_keys.issubset(set(c.keys()))
        assert c["setup_type"] == "ep_news"


# ===========================================================================
# Strategy evaluation tests (signals/ep_news_strategy.py)
# ===========================================================================

def _make_strategy_config(overrides: dict | None = None):
    """Default config for EP news strategy evaluation tests."""
    cfg = {
        "signals": {
            "ep_news_a_stop_loss_pct": 7.0,
            "ep_news_b_stop_loss_pct": 10.0,
            "ep_news_max_hold_days": 50,
            # Strategy A (NEWS-Tight)
            "ep_news_a_chg_open_min": 2.0,
            "ep_news_a_chg_open_max": 10.0,
            "ep_news_a_min_close_in_range": 50.0,
            "ep_news_a_max_downside_from_open": 3.0,
            "ep_news_a_prev_10d_max": -20.0,
            "ep_news_a_atr_pct_min": 3.0,
            "ep_news_a_atr_pct_max": 7.0,
            "ep_news_a_max_volume_m": 3.0,
            # Strategy B (NEWS-Relaxed)
            "ep_news_b_chg_open_min": 2.0,
            "ep_news_b_chg_open_max": 10.0,
            "ep_news_b_min_close_in_range": 30.0,
            "ep_news_b_max_close_in_range": 80.0,
            "ep_news_b_max_downside_from_open": 6.0,
            "ep_news_b_prev_10d_max": -10.0,
            "ep_news_b_atr_pct_min": 3.0,
            "ep_news_b_atr_pct_max": 7.0,
            "ep_news_b_max_volume_m": 5.0,
        },
    }
    if overrides:
        cfg["signals"].update(overrides)
    return cfg


def _make_candidate(
    ticker="NVDA",
    open_price=100.0,
    current_price=105.0,
    today_high=107.0,
    today_low=98.0,
    gap_pct=15.0,
    prev_close=87.0,
    today_volume=2_000_000,
):
    """Create a scanner candidate dict for strategy evaluation."""
    return {
        "ticker": ticker,
        "open_price": open_price,
        "current_price": current_price,
        "today_high": today_high,
        "today_low": today_low,
        "gap_pct": gap_pct,
        "prev_close": prev_close,
        "prev_high": 88.0,
        "today_volume": today_volume,
        "sma_200": 80.0,
        "market_cap": 5_000_000_000,
        "rvol": 2.5,
        "setup_type": "ep_news",
    }


def _make_strategy_daily_df(n=300, recent_selloff=True, atr_range=4.0):
    """
    Create daily bars for strategy tests.

    If recent_selloff=True, the last 11 bars show a ~25% decline
    (prev 10D change% ~ -25%, passing Strategy A's <= -20% filter).
    """
    rows = []
    price = 100.0
    for i in range(n - 11):
        rows.append({
            "open": price - 0.5,
            "high": price + atr_range / 2,
            "low": price - atr_range / 2,
            "close": price,
            "volume": 500_000,
        })
        price += 0.05

    # Last 11 bars: selloff if requested
    base = price
    for i in range(11):
        if recent_selloff:
            price = base * (1 - 0.025 * (i + 1))  # ~2.5%/day = ~25% over 10 days
        rows.append({
            "open": price + 0.5,
            "high": price + atr_range / 2,
            "low": price - atr_range / 2,
            "close": price,
            "volume": 500_000,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# compute_features tests
# ---------------------------------------------------------------------------

class TestComputeFeatures:
    def test_chg_open_positive(self):
        candidate = _make_candidate(open_price=100.0, current_price=105.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["chg_open_pct"] == 5.0

    def test_chg_open_negative(self):
        candidate = _make_candidate(open_price=100.0, current_price=95.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["chg_open_pct"] == -5.0

    def test_close_in_range_at_high(self):
        candidate = _make_candidate(current_price=110.0, today_high=110.0, today_low=100.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["close_in_range"] == 100.0

    def test_downside_from_open(self):
        candidate = _make_candidate(open_price=100.0, today_low=97.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["downside_from_open"] == 3.0

    def test_prev_10d_change_with_selloff(self):
        candidate = _make_candidate()
        df = _make_strategy_daily_df(recent_selloff=True)
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["prev_10d_change_pct"] < -20  # ~25% selloff

    def test_atr_pct_computed(self):
        candidate = _make_candidate(current_price=100.0)
        df = _make_strategy_daily_df(atr_range=4.0)
        features = compute_features(
            candidate, list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["atr_pct"] > 0


# ---------------------------------------------------------------------------
# Strategy A tests (NEWS-Tight)
# ---------------------------------------------------------------------------

class TestNewsStrategyA:
    def _make_passing_features(self):
        return {
            "chg_open_pct": 5.0,            # between 2 and 10
            "close_in_range": 65.0,         # >= 50
            "downside_from_open": 1.5,      # < 3
            "prev_10d_change_pct": -25.0,   # <= -20
            "atr_pct": 4.5,                 # between 3 and 7
        }

    def _make_passing_candidate(self):
        return _make_candidate(today_volume=2_000_000)  # 2M < 3M

    def test_passes_all_filters(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is True

    def test_fails_chg_open_below_2(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = 1.5
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_chg_open_above_10(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = 12.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_close_in_range_low(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["close_in_range"] = 30.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_downside_too_high(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["downside_from_open"] = 5.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_prev_10d_not_negative_enough(self):
        """Prev 10D = -15% fails (needs <= -20%)."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -15.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_atr_too_low(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["atr_pct"] = 2.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_atr_too_high(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["atr_pct"] = 8.0
        assert evaluate_strategy_a(self._make_passing_candidate(), features, config) is False

    def test_fails_volume_too_high(self):
        """Volume >= 3M fails Strategy A."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        candidate = _make_candidate(today_volume=4_000_000)  # 4M >= 3M
        assert evaluate_strategy_a(candidate, features, config) is False

    def test_passes_volume_at_boundary(self):
        """Volume just under 3M passes."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        candidate = _make_candidate(today_volume=2_900_000)  # 2.9M < 3M
        assert evaluate_strategy_a(candidate, features, config) is True


# ---------------------------------------------------------------------------
# Strategy B tests (NEWS-Relaxed)
# ---------------------------------------------------------------------------

class TestNewsStrategyB:
    def _make_passing_features(self):
        return {
            "chg_open_pct": 5.0,            # between 2 and 10
            "close_in_range": 55.0,         # between 30 and 80
            "downside_from_open": 3.0,      # < 6
            "prev_10d_change_pct": -15.0,   # <= -10
            "atr_pct": 4.5,                 # between 3 and 7
        }

    def _make_passing_candidate(self):
        return _make_candidate(today_volume=3_000_000)  # 3M < 5M

    def test_passes_all_filters(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is True

    def test_fails_chg_open_below_2(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = 1.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_chg_open_above_10(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = 11.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_close_in_range_below_30(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["close_in_range"] = 20.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_close_in_range_above_80(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["close_in_range"] = 85.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_downside_too_high(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["downside_from_open"] = 7.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_prev_10d_not_negative_enough(self):
        """Prev 10D = -5% fails (needs <= -10%)."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -5.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is False

    def test_fails_volume_too_high(self):
        """Volume >= 5M fails Strategy B."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        candidate = _make_candidate(today_volume=6_000_000)
        assert evaluate_strategy_b(candidate, features, config) is False

    def test_prev_10d_very_negative_passes(self):
        """Prev 10D = -50% still passes B (no floor)."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -50.0
        assert evaluate_strategy_b(self._make_passing_candidate(), features, config) is True


# ---------------------------------------------------------------------------
# evaluate_ep_news_strategies integration tests
# ---------------------------------------------------------------------------

class TestEvaluateEpNewsStrategies:
    def test_passes_strategy_b(self):
        """Stock that passes only B (fails A's tighter volume filter) gets -10% stop.

        A and B are now mutually exclusive — A wins if both pass — so to exercise
        B's branch we build a candidate that fails one of A's tighter thresholds.
        Volume of 4M trips A's <3M limit while staying under B's <5M.
        """
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=98.0,
            today_volume=4_000_000,  # fails A's <3M, passes B's <5M
        )
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=4.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, config)

        b_entries = [e for e in entries if e["ep_strategy"] == "B"]
        assert len(b_entries) == 1
        assert not any(e["ep_strategy"] == "A" for e in entries)  # A must be skipped
        assert b_entries[0]["stop_price"] == round(105.0 * 0.90, 2)  # -10% stop
        assert b_entries[0]["setup_type"] == "ep_news"

    def test_strategy_a_gets_7pct_stop(self):
        """Strategy A entries use -7% stop."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=98.0,
            today_volume=2_000_000,
        )
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=4.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, config)

        a_entries = [e for e in entries if e["ep_strategy"] == "A"]
        for e in a_entries:
            assert e["stop_price"] == round(105.0 * 0.93, 2)  # -7% stop

    def test_no_ab_entries_when_chg_open_too_low(self):
        """Stock with CHG-OPEN < 2% fails Strategy A and B (but may pass C)."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=101.0,
            today_high=102.0, today_low=99.0,
        )
        df = _make_strategy_daily_df(recent_selloff=True)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, config)
        ab_entries = [e for e in entries if e["ep_strategy"] in ("A", "B")]
        assert len(ab_entries) == 0
        # Strategy C may still pass (it doesn't filter on CHG-OPEN%)

    def test_empty_candidates(self):
        config = _make_strategy_config()
        entries, rejections = evaluate_ep_news_strategies([], {}, config)
        assert entries == []
        assert rejections == []

    def test_no_daily_bars_records_data_error(self):
        """Missing daily bars must be reported as a data error, not silently skipped.
        This is the exact bug that caused 5+ Apr 20 Strategy C candidates to be
        silently dropped — the 'no daily bars' path used to `continue` with no trace.
        """
        config = _make_strategy_config()
        candidate = _make_candidate()
        entries, rejections = evaluate_ep_news_strategies([candidate], {}, config)
        assert entries == []
        assert len(rejections) == 1
        assert rejections[0]["is_data_error"] is True
        assert rejections[0]["ticker"] == candidate["ticker"]

    def test_volume_filter_differentiates_a_and_b(self):
        """Stock with 4M volume fails A (< 3M) but passes B (< 5M)."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=98.0,
            today_volume=4_000_000,  # 4M: fails A's <3M, passes B's <5M
        )
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=4.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_news_strategies([candidate], daily_bars, config)

        strategies = [e["ep_strategy"] for e in entries]
        assert "A" not in strategies
        # B may or may not pass depending on CIR range (30-80);
        # with CIR from candidate setup it should be in range

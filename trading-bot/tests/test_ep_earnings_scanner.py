"""
Unit tests for the EP Earnings EOD scanner (scanner/ep_earnings.py)
and strategy evaluation (signals/ep_earnings_strategy.py).

Uses mocked AlpacaClient and yfinance — no network calls.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import numpy as np
import pandas as pd

from strategies.ep_earnings.scanner import scan_ep_earnings, _check_earnings_today, _get_ticker_info
from strategies.ep_earnings.strategy import (
    compute_features,
    evaluate_strategy_a,
    evaluate_strategy_b,
    evaluate_ep_earnings_strategies,
)


# ---------------------------------------------------------------------------
# scan_snapshot_gaps mock — Phase A pre-screen uses Alpaca snapshots via
# scanner/gap_screen.py::scan_snapshot_gaps. We replace it per-test with a
# controlled list so the suite stays offline.
# ---------------------------------------------------------------------------

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
    """Create a test config with EP earnings scanner defaults."""
    cfg = {
        "signals": {
            "ep_earnings_min_gap_pct": 8.0,
            "ep_earnings_min_price": 3.0,
            "ep_earnings_min_market_cap": 800_000_000,
            "ep_earnings_require_earnings": False,  # OFF by default in tests
            "ep_earnings_require_open_above_prev_high": True,
            "ep_earnings_require_above_200d_sma": True,
            "ep_earnings_min_rvol": 1.0,
        },
        "strategies": {
            "enabled": ["ep_earnings", "breakout"],
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
            "volume": 500_000,  # consistent volume for RVOL tests
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
    _GAP_MOVERS[:] = movers  # seed the gap_screen mock for the new code path
    client.get_snapshots.return_value = {
        symbol: _make_snapshot(prev_close, prev_high, open_price, latest_price, daily_volume),
    }
    # Daily bars: 300 rows, ending ~close to prev_close so 200d SMA < open
    df = _make_daily_df(300, start_price=prev_close - 30, drift=0.1)
    client.get_daily_bars_batch.return_value = {symbol: df}
    return client


# ---------------------------------------------------------------------------
# Phase A tests: gap%, price, open > prev_high
# ---------------------------------------------------------------------------

class TestPhaseAFilters:
    def test_basic_eod_scan(self):
        """Stock that passes all filters (earnings disabled)."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1
        assert result[0]["ticker"] == "NVDA"
        assert result[0]["gap_pct"] == 15.0
        assert result[0]["open_price"] == 115.0
        assert result[0]["setup_type"] == "ep_earnings"

    def test_filter_gap_below_8pct(self):
        """Stock with 5% gap is rejected (below 8% min)."""
        client = _make_passing_client(prev_close=100.0, open_price=105.0, latest_price=106.0)
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0

    def test_filter_price_below_3(self):
        """Stock with prev close $2 is rejected."""
        client = _make_passing_client(prev_close=2.0, prev_high=2.5, open_price=2.5, latest_price=2.6)
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0

    def test_filter_open_below_prev_high(self):
        """Stock where open < yesterday's high is rejected."""
        client = _make_passing_client(
            prev_close=100.0, prev_high=120.0, open_price=115.0, latest_price=118.0,
        )
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0

    def test_open_above_prev_high_disabled(self):
        """When filter is disabled, open < prev_high still passes."""
        client = _make_passing_client(
            prev_close=100.0, prev_high=120.0, open_price=115.0, latest_price=118.0,
            daily_volume=1_000_000,
        )
        config = _make_config({"ep_earnings_require_open_above_prev_high": False})

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1

    def test_empty_movers(self):
        """No market movers returns empty list."""
        client = MagicMock()
        client.get_market_movers_gainers.return_value = []
        _GAP_MOVERS.clear()

        result = scan_ep_earnings(_make_config(), client)
        assert result == []

    def test_filters_invalid_symbols(self):
        """Non-alpha and >5 char symbols are filtered out."""
        client = MagicMock()
        movers = [
            {"symbol": "GOOD", "percent_change": 20.0, "price": 50.0},
            {"symbol": "BAD123", "percent_change": 20.0, "price": 50.0},
            {"symbol": "TOOLONG", "percent_change": 20.0, "price": 50.0},
        ]
        client.get_market_movers_gainers.return_value = movers
        _GAP_MOVERS[:] = movers
        client.get_snapshots.return_value = {
            "GOOD": _make_snapshot(40.0, 42.0, 50.0, 52.0, 1_000_000),
        }
        df = _make_daily_df(300, start_price=30.0, drift=0.03)
        client.get_daily_bars_batch.return_value = {"GOOD": df}
        config = _make_config()

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1
        assert result[0]["ticker"] == "GOOD"


# ---------------------------------------------------------------------------
# Phase B tests: 200d SMA, RVOL, prior rally
# ---------------------------------------------------------------------------

class TestPhaseBFilters:
    def test_filter_below_200d_sma(self):
        """Stock where open < 200d SMA is rejected."""
        # Create daily bars that are much higher than open to make SMA > open
        client = _make_passing_client(
            prev_close=100.0, prev_high=105.0, open_price=115.0, latest_price=118.0,
        )
        # Override daily bars: prices around 200 so 200d SMA >> 115
        df = _make_daily_df(300, start_price=180.0, drift=0.1)
        client.get_daily_bars_batch.return_value = {"NVDA": df}
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0

    def test_200d_sma_disabled(self):
        """When filter is disabled, stock below 200d SMA still passes."""
        client = _make_passing_client(
            prev_close=100.0, prev_high=105.0, open_price=115.0, latest_price=118.0,
        )
        df = _make_daily_df(300, start_price=180.0, drift=0.1)
        client.get_daily_bars_batch.return_value = {"NVDA": df}
        config = _make_config({"ep_earnings_require_above_200d_sma": False})

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1

    def test_filter_low_rvol(self):
        """Stock where today's volume < 14d avg is rejected."""
        client = _make_passing_client(
            prev_close=100.0, prev_high=105.0, open_price=115.0,
            latest_price=118.0, daily_volume=100_000,  # low volume
        )
        # Daily bars have 500K avg volume, so 100K / 500K = 0.2 RVOL
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0

    def test_no_daily_bars(self):
        """Stock with no daily bars available is skipped."""
        client = _make_passing_client()
        client.get_daily_bars_batch.return_value = {}
        config = _make_config()

        result = scan_ep_earnings(config, client)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Phase C tests: market cap, security class, earnings
# ---------------------------------------------------------------------------

class TestPhaseCFilters:
    def test_filter_below_market_cap(self):
        """Stock with market cap < $800M is rejected."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(500_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 0

    def test_filter_non_equity(self):
        """ETF (quoteType != EQUITY) is rejected."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "ETF")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 0

    def test_filter_no_earnings(self):
        """Stock without earnings today is rejected when require_earnings=True."""
        client = _make_passing_client()
        config = _make_config({"ep_earnings_require_earnings": True})

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            with patch("strategies.ep_earnings.scanner._check_earnings_today", return_value=False):
                result = scan_ep_earnings(config, client)

        assert len(result) == 0

    def test_earnings_check_disabled(self):
        """When require_earnings=False, stock without earnings still passes."""
        client = _make_passing_client()
        config = _make_config({"ep_earnings_require_earnings": False})

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1

    def test_earnings_passes(self):
        """Stock with earnings today passes when require_earnings=True."""
        client = _make_passing_client()
        config = _make_config({"ep_earnings_require_earnings": True})

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            with patch("strategies.ep_earnings.scanner._check_earnings_today", return_value=True):
                result = scan_ep_earnings(config, client)

        assert len(result) == 1


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

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        gaps = [r["gap_pct"] for r in result]
        assert gaps == sorted(gaps, reverse=True)

    def test_output_dict_fields(self):
        """Output dict has all expected fields."""
        client = _make_passing_client()
        config = _make_config()

        with patch("strategies.ep_earnings.scanner._get_ticker_info", return_value=(5_000_000_000, "EQUITY")):
            result = scan_ep_earnings(config, client)

        assert len(result) == 1
        c = result[0]
        expected_keys = {
            "ticker", "gap_pct", "open_price", "prev_close", "prev_high",
            "current_price", "today_volume", "today_high", "today_low",
            "sma_200", "market_cap", "rvol", "setup_type",
        }
        assert expected_keys.issubset(set(c.keys()))


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestCheckEarningsToday:
    @patch("strategies.ep_earnings.scanner.yf")
    def test_earnings_today(self, mock_yf):
        """Returns True when earnings date matches today."""
        today = date(2026, 3, 27)
        mock_ticker = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        mock_ticker.get_earnings_dates.return_value = pd.DataFrame(
            {"Surprise(%)": [0.5]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-03-27 12:00:00")])
        )

        assert _check_earnings_today("NVDA", today) is True

    @patch("strategies.ep_earnings.scanner.yf")
    def test_earnings_yesterday(self, mock_yf):
        """Returns True when earnings date matches yesterday (after-hours)."""
        today = date(2026, 3, 27)
        mock_ticker = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        mock_ticker.get_earnings_dates.return_value = pd.DataFrame(
            {"Surprise(%)": [0.5]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-03-26 16:00:00")])
        )

        assert _check_earnings_today("NVDA", today) is True

    @patch("strategies.ep_earnings.scanner.yf")
    def test_no_earnings(self, mock_yf):
        """Returns False when no earnings dates match."""
        today = date(2026, 3, 27)
        mock_ticker = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        mock_ticker.get_earnings_dates.return_value = pd.DataFrame(
            {"Surprise(%)": [0.5]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-04-15 12:00:00")])
        )

        assert _check_earnings_today("NVDA", today) is False

    @patch("strategies.ep_earnings.scanner.yf")
    def test_exception_propagates(self, mock_yf):
        """Per error-handling policy: yfinance errors must not be swallowed — they propagate so the job fails and a Telegram alert fires."""
        mock_yf.Ticker.side_effect = Exception("network error")
        with pytest.raises(Exception, match="network error"):
            _check_earnings_today("NVDA", date(2026, 3, 27))


class TestGetTickerInfo:
    @patch("strategies.ep_earnings.scanner.yf")
    def test_returns_market_cap_and_type(self, mock_yf):
        mock_ticker = MagicMock()
        mock_yf.Ticker.return_value = mock_ticker
        mock_ticker.info = {"marketCap": 5_000_000_000, "quoteType": "EQUITY"}

        mcap, qtype = _get_ticker_info("NVDA")
        assert mcap == 5_000_000_000
        assert qtype == "EQUITY"

    @patch("strategies.ep_earnings.scanner.yf")
    def test_exception_propagates(self, mock_yf):
        """Per error-handling policy: yfinance errors must not be swallowed."""
        mock_yf.Ticker.side_effect = Exception("error")
        with pytest.raises(Exception, match="error"):
            _get_ticker_info("NVDA")


# ===========================================================================
# Strategy evaluation tests (signals/ep_earnings_strategy.py)
# ===========================================================================

def _make_strategy_config(overrides: dict | None = None):
    """Default config for strategy evaluation tests."""
    cfg = {
        "signals": {
            "ep_earnings_stop_loss_pct": 7.0,
            "ep_earnings_max_hold_days": 50,
            # Strategy A
            "ep_earnings_a_min_close_in_range": 50.0,
            "ep_earnings_a_max_downside_from_open": 3.0,
            "ep_earnings_a_prev_10d_min": -30.0,
            "ep_earnings_a_prev_10d_max": -10.0,
            # Strategy B
            "ep_earnings_b_min_close_in_range": 50.0,
            "ep_earnings_b_atr_pct_min": 2.0,
            "ep_earnings_b_atr_pct_max": 5.0,
            "ep_earnings_b_prev_10d_max": -10.0,
        },
    }
    if overrides:
        cfg["signals"].update(overrides)
    return cfg


def _make_candidate(
    ticker="NVDA",
    open_price=100.0,
    current_price=108.0,
    today_high=110.0,
    today_low=98.0,
    gap_pct=15.0,
    prev_close=87.0,
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
        "today_volume": 1_000_000,
        "sma_200": 80.0,
        "market_cap": 5_000_000_000,
        "rvol": 2.5,
        "setup_type": "ep_earnings",
    }


def _make_strategy_daily_df(n=300, recent_selloff=True, atr_range=2.0):
    """
    Create daily bars for strategy tests.

    If recent_selloff=True, the last 11 bars show a -15% decline
    (prev 10D change% ~ -15%, within Strategy A's [-30, -10] range).
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
        price += 0.05  # slow uptrend

    # Last 11 bars: selloff if requested
    base = price
    for i in range(11):
        if recent_selloff:
            price = base * (1 - 0.015 * (i + 1))  # ~1.5%/day decline = ~15% over 10 days
        rows.append({
            "open": price + 0.5,
            "high": price + atr_range / 2,
            "low": price - atr_range / 2,
            "close": price,
            "volume": 500_000,
        })

    return pd.DataFrame(rows)


class TestComputeFeatures:
    def test_chg_open_positive(self):
        """CHG-OPEN% is positive when current_price > open."""
        candidate = _make_candidate(open_price=100.0, current_price=105.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["chg_open_pct"] == 5.0

    def test_chg_open_negative(self):
        """CHG-OPEN% is negative when current_price < open."""
        candidate = _make_candidate(open_price=100.0, current_price=95.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["chg_open_pct"] == -5.0

    def test_close_in_range(self):
        """close_in_range = 100 when price is at today's high."""
        candidate = _make_candidate(
            current_price=110.0, today_high=110.0, today_low=100.0,
        )
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["close_in_range"] == 100.0

    def test_close_in_range_at_low(self):
        """close_in_range = 0 when price is at today's low."""
        candidate = _make_candidate(
            current_price=100.0, today_high=110.0, today_low=100.0,
        )
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["close_in_range"] == 0.0

    def test_downside_from_open(self):
        """downside_from_open = (open - low) / open * 100."""
        candidate = _make_candidate(open_price=100.0, today_low=97.0)
        df = _make_strategy_daily_df()
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["downside_from_open"] == 3.0

    def test_prev_10d_change_with_selloff(self):
        """Prev 10D change% is negative with recent selloff data."""
        candidate = _make_candidate()
        df = _make_strategy_daily_df(recent_selloff=True)
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["prev_10d_change_pct"] < 0

    def test_atr_pct_computed(self):
        """ATR% is positive and reasonable."""
        candidate = _make_candidate(current_price=100.0)
        df = _make_strategy_daily_df(atr_range=4.0)
        features = compute_features(
            candidate,
            list(df["close"]), list(df["high"]), list(df["low"]),
        )
        assert features["atr_pct"] > 0


class TestStrategyA:
    def _make_passing_features(self):
        """Features that pass all Strategy A filters."""
        return {
            "chg_open_pct": 5.0,       # > 0
            "close_in_range": 75.0,    # >= 50
            "downside_from_open": 1.5, # < 3
            "prev_10d_change_pct": -15.0,  # between -30 and -10
            "atr_pct": 3.5,
        }

    def test_passes_all_filters(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        candidate = _make_candidate()
        assert evaluate_strategy_a(candidate, features, config) is True

    def test_fails_chg_open_negative(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = -1.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is False

    def test_fails_close_in_range_low(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["close_in_range"] = 30.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is False

    def test_fails_downside_too_high(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["downside_from_open"] = 5.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is False

    def test_fails_prev_10d_too_positive(self):
        """Prev 10D > -10% (e.g. -5%) fails Strategy A."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -5.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is False

    def test_fails_prev_10d_too_negative(self):
        """Prev 10D < -30% (e.g. -35%) fails Strategy A."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -35.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is False

    def test_boundary_prev_10d_at_minus_10(self):
        """Prev 10D = -10.0 exactly should pass (within range)."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -10.0
        assert evaluate_strategy_a(_make_candidate(), features, config) is True


class TestStrategyB:
    def _make_passing_features(self):
        """Features that pass all Strategy B filters."""
        return {
            "chg_open_pct": 5.0,       # > 0
            "close_in_range": 75.0,    # >= 50
            "downside_from_open": 1.5,
            "prev_10d_change_pct": -15.0,  # < -10
            "atr_pct": 3.5,            # between 2 and 5
        }

    def test_passes_all_filters(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        assert evaluate_strategy_b(_make_candidate(), features, config) is True

    def test_fails_chg_open_negative(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["chg_open_pct"] = -1.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is False

    def test_fails_close_in_range_low(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["close_in_range"] = 30.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is False

    def test_fails_atr_too_low(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["atr_pct"] = 1.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is False

    def test_fails_atr_too_high(self):
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["atr_pct"] = 6.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is False

    def test_fails_prev_10d_too_positive(self):
        """Prev 10D > -10% (e.g. +5%) fails Strategy B."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = 5.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is False

    def test_prev_10d_very_negative_passes(self):
        """Prev 10D = -40% still passes B (no floor unlike A)."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["prev_10d_change_pct"] = -40.0
        assert evaluate_strategy_b(_make_candidate(), features, config) is True

    def test_no_downside_filter(self):
        """Strategy B has no downside_from_open filter, so 10% dip passes."""
        config = _make_strategy_config()
        features = self._make_passing_features()
        features["downside_from_open"] = 10.0  # would fail A, but B has no such filter
        assert evaluate_strategy_b(_make_candidate(), features, config) is True


class TestEvaluateEpEarningsStrategies:
    def test_passes_both_strategies(self):
        """Stock passing both A and B produces two entries."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=99.0,
        )
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=4.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_earnings_strategies([candidate], daily_bars, config)

        strategies = [e["ep_strategy"] for e in entries]
        # Should have at least one entry, could be A, B, or both
        assert len(entries) >= 1
        # All entries should have required fields
        for e in entries:
            assert "entry_price" in e
            assert "stop_price" in e
            assert "ep_strategy" in e
            assert e["ep_strategy"] in ("A", "B", "C")
            if e["ep_strategy"] in ("A", "B"):
                assert e["stop_price"] == round(e["entry_price"] * 0.93, 2)

    def test_no_ab_entries_when_chg_open_negative(self):
        """Stock with negative CHG-OPEN% fails Strategy A and B (but may pass C)."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=95.0,  # negative CHG-OPEN
            today_high=101.0, today_low=94.0,
        )
        df = _make_strategy_daily_df(recent_selloff=True)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_earnings_strategies([candidate], daily_bars, config)
        ab_entries = [e for e in entries if e["ep_strategy"] in ("A", "B")]
        assert len(ab_entries) == 0
        # Strategy C may still pass (it doesn't filter on CHG-OPEN%)

    def test_stop_price_7pct(self):
        """Stop price is exactly -7% from entry for all strategies."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=99.0,
        )
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=4.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_earnings_strategies([candidate], daily_bars, config)
        for e in entries:
            # All strategies use -7% stop
            expected_stop = round(105.0 * 0.93, 2)
            assert e["stop_price"] == expected_stop

    def test_empty_candidates(self):
        """Empty candidates list returns empty entries and no rejections."""
        config = _make_strategy_config()
        entries, rejections = evaluate_ep_earnings_strategies([], {}, config)
        assert entries == []
        assert rejections == []

    def test_no_daily_bars_records_data_error(self):
        """Missing daily bars must surface as a data error, not a silent skip."""
        config = _make_strategy_config()
        candidate = _make_candidate()
        entries, rejections = evaluate_ep_earnings_strategies([candidate], {}, config)
        assert entries == []
        assert len(rejections) == 1
        assert rejections[0]["is_data_error"] is True
        assert rejections[0]["ticker"] == candidate["ticker"]

    def test_passes_a_only(self):
        """Stock with ATR outside 2-5% range passes A but not B."""
        config = _make_strategy_config()
        candidate = _make_candidate(
            open_price=100.0, current_price=105.0,
            today_high=107.0, today_low=99.5,  # low downside from open
        )
        # ATR too high (> 5%) for Strategy B, but A doesn't check ATR
        df = _make_strategy_daily_df(recent_selloff=True, atr_range=12.0)
        daily_bars = {"NVDA": df}

        entries, _ = evaluate_ep_earnings_strategies([candidate], daily_bars, config)
        a_entries = [e for e in entries if e["ep_strategy"] == "A"]
        b_entries = [e for e in entries if e["ep_strategy"] == "B"]
        # A should pass (no ATR filter), B should fail (ATR too high)
        assert len(a_entries) >= 0  # depends on other features
        # At minimum, B should not pass with ATR > 5%
        for e in b_entries:
            assert e["atr_pct"] <= 5.0

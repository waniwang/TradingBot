"""
Daily-bar backtest engine.

Simulates trading strategies bar-by-bar using daily OHLCV data.
Since we don't have intraday bars for history, entry/exit conditions
are approximated from daily bars.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backtest.metrics import Trade, compute_metrics
from signals.base import compute_sma, compute_atr_from_list

logger = logging.getLogger(__name__)


@dataclass
class BacktestPosition:
    """An open position in the backtest."""

    ticker: str
    setup_type: str
    side: str            # "long" or "short"
    entry_date: str
    entry_price: float
    stop_price: float
    shares: int
    days_held: int = 0
    partial_exit_done: bool = False
    partial_exit_shares: int = 0
    partial_exit_price: float | None = None


@dataclass
class BacktestConfig:
    """Configuration for the backtest engine."""

    initial_capital: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    max_positions: int = 4
    partial_exit_after_days: int = 3
    partial_exit_gain_pct: float = 15.0
    partial_exit_fraction: float = 0.40
    trailing_ma_period: int = 10

    # Breakout setup params
    breakout_consolidation_days: int = 20
    breakout_lookback: int = 5
    breakout_volume_multiplier: float = 1.5
    breakout_prior_move_pct: float = 30.0
    breakout_atr_contraction_ratio: float = 0.85
    breakout_ma_tolerance_pct: float = 3.0
    breakout_stop_atr_mult: float = 1.0

    # EP setup params
    ep_min_gap_pct: float = 10.0
    ep_volume_multiplier: float = 2.0
    ep_prior_rally_max_pct: float = 50.0
    ep_entry_range_fraction: float = 0.30
    ep_stop_atr_mult: float = 1.5

    # Parabolic setup params
    parabolic_min_gain_pct: float = 50.0
    parabolic_min_days: int = 3
    parabolic_target_ma_short: int = 10
    parabolic_target_ma_long: int = 20

    # General
    atr_period: int = 14
    warmup_days: int = 130
    max_position_pct: float = 25.0
    slippage_bps: float = 0.0
    shuffle_seed: int | None = 42


class BacktestRunner:
    """
    Daily-bar backtest engine.

    Simulates trading strategies bar-by-bar using daily OHLCV data.
    Delegates entry/exit logic to strategy plugins when available.
    """

    def __init__(self, config: BacktestConfig | None = None, plugins: dict | None = None):
        self.config = config or BacktestConfig()
        self.portfolio_value: float = self.config.initial_capital
        self.cash: float = self.config.initial_capital
        self.positions: list[BacktestPosition] = []
        self.trades: list[Trade] = []
        self.daily_equity: list[float] = []
        self._plugins = plugins or {}  # {name: StrategyPlugin}

    def run(
        self,
        universe_bars: dict[str, pd.DataFrame],
        setups: list[str] | None = None,
    ) -> dict:
        """
        Run the backtest over all trading days.

        Args:
            universe_bars: dict of ticker -> DataFrame with
                columns [date, open, high, low, close, volume]
            setups: list of setup types to test
                (default: all three)

        Returns:
            dict of performance metrics
        """
        if setups is None:
            setups = ["breakout", "episodic_pivot", "parabolic_short"]

        # Build a sorted list of all trading dates across the universe
        all_dates = set()
        for df in universe_bars.values():
            if "date" in df.columns:
                all_dates.update(pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"))
        trading_days = sorted(all_dates)

        if not trading_days:
            return compute_metrics(self.trades, self.daily_equity, self.config.initial_capital)

        # Pre-index data by ticker for fast lookups
        ticker_data: dict[str, pd.DataFrame] = {}
        for ticker, df in universe_bars.items():
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                df = df.set_index("date").sort_index()
            ticker_data[ticker] = df

        # Record initial equity
        self.daily_equity.append(self.config.initial_capital)

        for i, date in enumerate(trading_days):
            # Skip warmup period to have enough history
            if i < self.config.warmup_days:
                self.daily_equity.append(self._compute_equity(ticker_data, date))
                continue

            prior_dates = trading_days[:i]

            # 1. Exit checks on open positions
            self._process_exits(date, ticker_data, prior_dates)

            # 2. Recompute equity for position sizing
            self.portfolio_value = self._compute_equity(ticker_data, date)

            # 3. Scanner + entry phase
            if len(self.positions) < self.config.max_positions:
                self._scan_and_enter(date, ticker_data, prior_dates, setups)

            # 4. Record daily equity
            equity = self._compute_equity(ticker_data, date)
            self.daily_equity.append(equity)

        # Close any remaining open positions at last price
        self._close_all_remaining(trading_days[-1], ticker_data)

        return compute_metrics(
            self.trades, self.daily_equity, self.config.initial_capital
        )

    def _compute_equity(self, ticker_data: dict, date: str) -> float:
        """Compute total equity (cash + market value of open positions)."""
        equity = self.cash
        for pos in self.positions:
            df = ticker_data.get(pos.ticker)
            if df is None or date not in df.index:
                price = pos.entry_price
            else:
                price = float(df.loc[date, "close"])
            remaining = pos.shares - pos.partial_exit_shares
            if pos.side == "long":
                equity += remaining * price
            else:
                # Short: unrealized P&L = (entry - current) * shares
                # Cash already includes sale proceeds, so add the liability
                equity -= remaining * price
        return equity

    def _process_exits(self, date: str, ticker_data: dict, prior_dates: list[str]):
        """Check stops, trailing MA, partial exits for all open positions."""
        to_close: list[tuple[BacktestPosition, float, str]] = []

        for pos in self.positions:
            pos.days_held += 1
            df = ticker_data.get(pos.ticker)
            if df is None or date not in df.index:
                continue

            row = df.loc[date]
            close = float(row["close"])
            low = float(row["low"])
            high = float(row["high"])
            today_open = float(row["open"])

            # Stop check — use gap-adjusted fill price (open if gapped through stop)
            if pos.side == "long" and low <= pos.stop_price:
                fill_price = min(today_open, pos.stop_price) if today_open < pos.stop_price else pos.stop_price
                to_close.append((pos, fill_price, "stop_hit"))
                continue
            elif pos.side == "short" and high >= pos.stop_price:
                fill_price = max(today_open, pos.stop_price) if today_open > pos.stop_price else pos.stop_price
                to_close.append((pos, fill_price, "stop_hit"))
                continue

            # Strategy-specific exit hook (replaces hardcoded parabolic branch)
            plugin = self._plugins.get(pos.setup_type)
            if plugin is not None:
                closes_for_exit = self._get_recent_closes(
                    pos.ticker, date, ticker_data, prior_dates,
                    max(self.config.parabolic_target_ma_long, self.config.trailing_ma_period),
                )
                history_for_exit = {"closes": closes_for_exit}
                exit_result = plugin.backtest_exit(pos, date, row, history_for_exit, self.config)
                if exit_result is not None:
                    exit_price, reason = exit_result
                    to_close.append((pos, exit_price, reason))
                    continue
                # Parabolic short partial exit at short MA (handled inline since
                # backtest_exit returns None to let shared logic handle partial)
                if pos.setup_type == "parabolic_short" and pos.side == "short" and not pos.partial_exit_done:
                    ma_short = self.config.parabolic_target_ma_short
                    if len(closes_for_exit) >= ma_short:
                        ma_s = compute_sma(closes_for_exit, ma_short)
                        if ma_s is not None and close <= ma_s:
                            self._do_partial_exit(pos, close, date)
                            continue

            # Trailing MA close exit (only after partial exit done)
            if pos.partial_exit_done:
                closes = self._get_recent_closes(
                    pos.ticker, date, ticker_data, prior_dates,
                    self.config.trailing_ma_period,
                )
                if len(closes) >= self.config.trailing_ma_period:
                    ma = compute_sma(closes, self.config.trailing_ma_period)
                    if ma is not None:
                        if pos.side == "long" and close < ma:
                            to_close.append((pos, close, "trailing_ma_close"))
                            continue
                        elif pos.side == "short" and close > ma:
                            to_close.append((pos, close, "trailing_ma_close"))
                            continue

            # Partial exit check
            if not pos.partial_exit_done and pos.setup_type != "parabolic_short":
                gain_pct = self._gain_pct(pos, close)
                if (
                    pos.days_held >= self.config.partial_exit_after_days
                    and gain_pct >= self.config.partial_exit_gain_pct
                ):
                    self._do_partial_exit(pos, close, date)
                    # Move stop to break-even
                    pos.stop_price = pos.entry_price

        for pos, price, reason in to_close:
            self._close_position(pos, price, date, reason)

    def _scan_and_enter(
        self, date: str, ticker_data: dict, prior_dates: list[str],
        setups: list[str],
    ):
        """Scan for entry candidates and open positions."""
        cfg = self.config
        already_held = {p.ticker for p in self.positions}

        # Shuffle ticker order to avoid alphabetical bias (seeded for reproducibility)
        tickers = list(ticker_data.keys())
        if cfg.shuffle_seed is not None:
            rng = random.Random(cfg.shuffle_seed + hash(date))
            rng.shuffle(tickers)
        else:
            random.shuffle(tickers)

        for ticker in tickers:
            df = ticker_data[ticker]
            if len(self.positions) >= cfg.max_positions:
                break
            if ticker in already_held:
                continue
            if date not in df.index:
                continue

            row = df.loc[date]
            today_close = float(row["close"])
            today_open = float(row["open"])
            today_high = float(row["high"])
            today_low = float(row["low"])
            today_volume = float(row["volume"])

            closes = self._get_recent_closes(ticker, date, ticker_data, prior_dates, cfg.warmup_days)
            if len(closes) < 30:
                continue

            highs = self._get_recent_values(ticker, date, ticker_data, prior_dates, cfg.warmup_days, "high")
            lows = self._get_recent_values(ticker, date, ticker_data, prior_dates, cfg.warmup_days, "low")
            volumes = self._get_recent_values(ticker, date, ticker_data, prior_dates, 20, "volume")
            avg_vol = float(np.mean(volumes)) if volumes else 0

            # Check each enabled strategy via plugin dispatch
            history = {
                "closes": closes,
                "highs": highs,
                "lows": lows,
                "volumes": volumes,
                "avg_vol": avg_vol,
            }
            entered = False
            for setup_name in setups:
                if len(self.positions) >= cfg.max_positions:
                    break
                plugin = self._plugins.get(setup_name)
                if plugin is not None:
                    result = plugin.backtest_entry(ticker, date, row, history, cfg)
                    if result is not None:
                        shares = self._size_position(result.entry_price, result.stop_price)
                        if shares > 0:
                            self._open_position(
                                ticker, setup_name, result.side, date,
                                result.entry_price, result.stop_price, shares,
                            )
                            entered = True
                            break
                else:
                    # Fallback: use legacy methods for strategies without plugins
                    if setup_name == "breakout" and self._check_breakout_entry(
                        ticker, date, today_high, today_low, today_close, today_volume,
                        closes, highs, lows, volumes, avg_vol, prior_dates, ticker_data,
                    ):
                        entered = True
                        break
                    elif setup_name == "episodic_pivot" and len(closes) >= 2:
                        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
                        gap_pct = (today_open - prev_close) / prev_close * 100 if prev_close > 0 else 0
                        if self._check_ep_entry(
                            ticker, date, gap_pct, today_open, today_high, today_low,
                            today_volume, closes, highs, lows, avg_vol,
                        ):
                            entered = True
                            break
                    elif setup_name == "parabolic_short":
                        if self._check_parabolic_entry(
                            ticker, date, today_open, today_high, today_low, today_close,
                            today_volume, closes, highs, lows, avg_vol,
                        ):
                            entered = True
                            break

    def _check_breakout_entry(
        self, ticker, date, today_high, today_low, today_close, today_volume,
        closes, highs, lows, volumes, avg_vol, prior_dates, ticker_data,
    ) -> bool:
        """Check breakout conditions. Returns True if entry was made."""
        cfg = self.config

        # Need enough history for consolidation check
        if len(closes) < cfg.breakout_consolidation_days + 60:
            return False

        # Prior large move check (30%+ in 2 months before consolidation)
        consol_end = len(closes) - cfg.breakout_consolidation_days
        lookback = min(consol_end, 60)
        if lookback > 10:
            prior = closes[consol_end - lookback : consol_end]
            if len(prior) > 0:
                move = (max(prior) - min(prior)) / min(prior) * 100
                if move < cfg.breakout_prior_move_pct:
                    return False

        # ATR contraction in consolidation window
        consol_highs = highs[-cfg.breakout_consolidation_days:]
        consol_lows = lows[-cfg.breakout_consolidation_days:]
        consol_closes = closes[-cfg.breakout_consolidation_days:]
        if len(consol_highs) < cfg.breakout_consolidation_days:
            return False

        recent_ranges = [h - l for h, l in zip(consol_highs[-10:], consol_lows[-10:])]
        older_ranges = [h - l for h, l in zip(consol_highs[:10], consol_lows[:10])]
        avg_recent = np.mean(recent_ranges) if recent_ranges else 1
        avg_older = np.mean(older_ranges) if older_ranges else 1
        if avg_older == 0 or avg_recent / avg_older > cfg.breakout_atr_contraction_ratio:
            return False

        # Near both 10d and 20d MA
        ma10 = compute_sma(closes[:-1], 10)  # use prior day for MA
        ma20 = compute_sma(closes[:-1], 20)
        if ma10 is None or ma20 is None:
            return False
        prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
        ma_tol = cfg.breakout_ma_tolerance_pct / 100.0
        if abs(prev_close - ma10) / ma10 > ma_tol:
            return False
        if abs(prev_close - ma20) / ma20 > ma_tol:
            return False

        # Breakout: today's high > max of prior 5 days
        if len(highs) < cfg.breakout_lookback + 1:
            return False
        prior_highs = highs[-(cfg.breakout_lookback + 1):-1]
        resistance = max(prior_highs)
        if today_high <= resistance:
            return False

        # Volume check
        if avg_vol > 0 and today_volume / avg_vol < cfg.breakout_volume_multiplier:
            return False

        # Entry
        entry_price = resistance  # breakout price
        stop_price = today_low  # LOD

        # ATR cap on stop
        atr = compute_atr_from_list(highs, lows, closes)
        if atr is not None and (entry_price - stop_price) > cfg.breakout_stop_atr_mult * atr:
            stop_price = entry_price - cfg.breakout_stop_atr_mult * atr

        if stop_price >= entry_price:
            return False

        shares = self._size_position(entry_price, stop_price)
        if shares <= 0:
            return False

        self._open_position(ticker, "breakout", "long", date, entry_price, stop_price, shares)
        return True

    def _check_ep_entry(
        self, ticker, date, gap_pct, today_open, today_high, today_low,
        today_volume, closes, highs, lows, avg_vol,
    ) -> bool:
        """Check episodic pivot conditions. Returns True if entry was made."""
        cfg = self.config

        if gap_pct < cfg.ep_min_gap_pct:
            return False

        # Volume check
        if avg_vol > 0 and today_volume / avg_vol < cfg.ep_volume_multiplier:
            return False

        # Prior rally filter: reject if already up 50%+ in prior 6 months
        if len(closes) >= 130:
            prior_gain = (closes[-2] - closes[0]) / closes[0] * 100
            if prior_gain >= cfg.ep_prior_rally_max_pct:
                return False

        # Entry: approximate ORH breakout
        entry_price = today_open + (today_high - today_open) * cfg.ep_entry_range_fraction
        if entry_price <= 0 or today_high < entry_price:
            return False

        stop_price = today_low

        # ATR cap
        atr = compute_atr_from_list(highs, lows, closes)
        if atr is not None and (entry_price - stop_price) > cfg.ep_stop_atr_mult * atr:
            stop_price = entry_price - cfg.ep_stop_atr_mult * atr

        if stop_price >= entry_price:
            return False

        shares = self._size_position(entry_price, stop_price)
        if shares <= 0:
            return False

        self._open_position(ticker, "episodic_pivot", "long", date, entry_price, stop_price, shares)
        return True

    def _check_parabolic_entry(
        self, ticker, date, today_open, today_high, today_low, today_close,
        today_volume, closes, highs, lows, avg_vol,
    ) -> bool:
        """Check parabolic short conditions. Returns True if entry was made."""
        cfg = self.config

        if len(closes) < cfg.parabolic_min_days + 1:
            return False

        base_price = closes[-(cfg.parabolic_min_days + 1)]
        recent_high = max(highs[-cfg.parabolic_min_days:])
        if base_price <= 0:
            return False

        gain_pct = (recent_high - base_price) / base_price * 100
        if gain_pct < cfg.parabolic_min_gain_pct:
            return False

        # Reversal day: red candle
        if today_close >= today_open:
            return False

        # Entry at today's close (proxy for ORB low short)
        entry_price = today_close
        stop_price = today_high

        if stop_price <= entry_price:
            return False

        shares = self._size_position(entry_price, stop_price)
        if shares <= 0:
            return False

        self._open_position(ticker, "parabolic_short", "short", date, entry_price, stop_price, shares)
        return True

    def _get_recent_closes(
        self, ticker: str, date: str, ticker_data: dict,
        prior_dates: list[str], n: int,
    ) -> list[float]:
        """Get the last n close prices ending on `date` (inclusive)."""
        df = ticker_data.get(ticker)
        if df is None:
            return []
        mask = df.index <= date
        recent = df.loc[mask].tail(n)
        return recent["close"].tolist()

    def _get_recent_values(
        self, ticker: str, date: str, ticker_data: dict,
        prior_dates: list[str], n: int, column: str,
    ) -> list[float]:
        """Get the last n values of a column ending on `date` (inclusive)."""
        df = ticker_data.get(ticker)
        if df is None:
            return []
        mask = df.index <= date
        recent = df.loc[mask].tail(n)
        return recent[column].tolist()

    def _gain_pct(self, pos: BacktestPosition, current_price: float) -> float:
        if pos.side == "long":
            return (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            return (pos.entry_price - current_price) / pos.entry_price * 100

    def _apply_slippage(self, price: float, side: str, direction: str) -> float:
        """Apply slippage to a price. direction is 'entry' or 'exit'."""
        if self.config.slippage_bps == 0:
            return price
        slip = price * self.config.slippage_bps / 10_000
        # Slippage always costs: buy higher, sell lower
        if (side == "long" and direction == "entry") or (side == "short" and direction == "exit"):
            return price + slip
        return price - slip

    def _size_position(self, entry_price: float, stop_price: float) -> int:
        """Calculate position size based on risk using portfolio value."""
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return 0
        max_risk = self.portfolio_value * (self.config.risk_per_trade_pct / 100.0)
        shares = int(max_risk / risk_per_share)
        # Cap notional
        max_notional = self.portfolio_value * (self.config.max_position_pct / 100.0)
        max_shares = int(max_notional / entry_price) if entry_price > 0 else 0
        return min(shares, max_shares)

    def _open_position(
        self, ticker: str, setup_type: str, side: str,
        date: str, entry_price: float, stop_price: float, shares: int,
    ):
        # Apply slippage to entry
        entry_price = self._apply_slippage(entry_price, side, "entry")

        pos = BacktestPosition(
            ticker=ticker,
            setup_type=setup_type,
            side=side,
            entry_date=date,
            entry_price=entry_price,
            stop_price=stop_price,
            shares=shares,
        )
        self.positions.append(pos)
        cost = shares * entry_price
        if side == "long":
            self.cash -= cost
        else:
            # Short sale: receive proceeds
            self.cash += cost
        logger.debug(
            "OPEN %s %s %d @ %.2f stop=%.2f [%s]",
            side, ticker, shares, entry_price, stop_price, date,
        )

    def _do_partial_exit(self, pos: BacktestPosition, price: float, date: str):
        price = self._apply_slippage(price, pos.side, "exit")
        shares_to_sell = max(1, int(pos.shares * self.config.partial_exit_fraction))
        pos.partial_exit_done = True
        pos.partial_exit_shares = shares_to_sell
        pos.partial_exit_price = price
        # Credit cash
        if pos.side == "long":
            self.cash += shares_to_sell * price
        else:
            # Short: buy back shares at current price
            self.cash -= shares_to_sell * price

    def _close_position(
        self, pos: BacktestPosition, exit_price: float,
        date: str, reason: str,
    ):
        exit_price = self._apply_slippage(exit_price, pos.side, "exit")
        remaining = pos.shares - pos.partial_exit_shares

        # Calculate remaining shares P&L
        if pos.side == "long":
            remaining_pnl = remaining * (exit_price - pos.entry_price)
        else:
            remaining_pnl = remaining * (pos.entry_price - exit_price)

        # Calculate partial exit P&L
        partial_pnl = 0.0
        if pos.partial_exit_done and pos.partial_exit_price is not None:
            if pos.side == "long":
                partial_pnl = pos.partial_exit_shares * (pos.partial_exit_price - pos.entry_price)
            else:
                partial_pnl = pos.partial_exit_shares * (pos.entry_price - pos.partial_exit_price)

        total_pnl = remaining_pnl + partial_pnl

        # Credit remaining to cash
        if pos.side == "long":
            self.cash += remaining * exit_price
        else:
            # Short: buy back remaining shares
            self.cash -= remaining * exit_price

        trade = Trade(
            ticker=pos.ticker,
            setup_type=pos.setup_type,
            side=pos.side,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=pos.shares,
            pnl=total_pnl,
            exit_reason=reason,
        )
        self.trades.append(trade)
        self.positions.remove(pos)

        logger.debug(
            "CLOSE %s %s @ %.2f pnl=%.2f reason=%s [%s]",
            pos.ticker, pos.side, exit_price, trade.pnl, reason, date,
        )

    def _close_all_remaining(self, date: str, ticker_data: dict):
        """Close all open positions at last available price."""
        for pos in list(self.positions):
            df = ticker_data.get(pos.ticker)
            if df is not None and date in df.index:
                price = float(df.loc[date, "close"])
            else:
                price = pos.entry_price
            self._close_position(pos, price, date, "backtest_end")

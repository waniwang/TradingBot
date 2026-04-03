"""Parabolic Short strategy plugin."""

from __future__ import annotations

from core.loader import ExitAction, BacktestEntryResult


class ParabolicShortPlugin:
    """
    Parabolic Short — short setup on overextended stocks.

    Scans for stocks with multi-day parabolic moves (50%+ gain).
    Enters short on ORB low break with VWAP failure confirmation.
    Custom exit: cover at 10d/20d MA profit targets.
    Single-day candidates — expire at EOD if not triggered.
    """

    name = "parabolic_short"
    display_name = "Parabolic Short"
    watchlist_persist_days = 1  # single-day: expire at EOD
    schedule = []  # no extra cron jobs

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        from strategies.parabolic_short.scanner import scan_parabolic_candidates

        cfg = config.get("strategies", {}).get("parabolic_short", {})
        return scan_parabolic_candidates(cfg, client)

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        from strategies.parabolic_short.signal import check_parabolic_short

        cfg = ctx.get("config", {}).get("strategies", {}).get("parabolic_short", {})
        return check_parabolic_short(
            ticker,
            candles_1m=ctx["candles_1m"],
            daily_closes=ctx["daily_closes"],
            current_price=ctx["current_price"],
            current_volume=ctx["current_volume"],
            config=cfg,
            daily_highs=ctx.get("daily_highs"),
        )

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        from strategies.parabolic_short.exits import check_parabolic_target

        return check_parabolic_target(pos, current_price, daily_closes)

    def backtest_entry(self, ticker, date, row, history, bt_config) -> BacktestEntryResult | None:
        from strategies.parabolic_short.backtest import check_entry

        return check_entry(ticker, date, row, history, bt_config)

    def backtest_exit(self, pos, date, row, history, bt_config):
        from strategies.parabolic_short.backtest import check_exit

        return check_exit(pos, date, row, history, bt_config)


PLUGIN = ParabolicShortPlugin()

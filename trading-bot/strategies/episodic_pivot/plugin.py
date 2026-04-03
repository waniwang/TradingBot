"""Episodic Pivot strategy plugin."""

from __future__ import annotations

from core.loader import ExitAction, BacktestEntryResult


class EpisodicPivotPlugin:
    """
    Episodic Pivot (EP) — long setup triggered by unexpected catalysts.

    Scans for stocks gapping up significantly in premarket (earnings, news).
    Enters on ORH breakout with elevated RVOL.
    Single-day candidates — expire at EOD if not triggered.
    Uses shared exit logic (stop, partial, trailing MA close).
    """

    name = "episodic_pivot"
    display_name = "Episodic Pivot"
    watchlist_persist_days = 1  # single-day: expire at EOD
    schedule = []  # no extra cron jobs — only premarket scan

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        from strategies.episodic_pivot.scanner import get_premarket_gappers

        cfg = config.get("strategies", {}).get("episodic_pivot", {})
        return get_premarket_gappers(cfg, client)

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        from strategies.episodic_pivot.signal import check_episodic_pivot

        cfg = ctx.get("config", {}).get("strategies", {}).get("episodic_pivot", {})
        return check_episodic_pivot(
            ticker,
            candles_1m=ctx["candles_1m"],
            daily_volumes=ctx["daily_volumes"],
            current_price=ctx["current_price"],
            current_volume=ctx["current_volume"],
            gap_pct=watchlist_entry.get("gap_pct", 0.0),
            config=cfg,
            daily_highs=ctx.get("daily_highs"),
            daily_lows=ctx.get("daily_lows"),
            daily_closes=ctx.get("daily_closes"),
            minutes_since_open=ctx.get("minutes_since_open"),
        )

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        return None  # use shared exit logic

    def backtest_entry(self, ticker, date, row, history, bt_config) -> BacktestEntryResult | None:
        from strategies.episodic_pivot.backtest import check_entry

        return check_entry(ticker, date, row, history, bt_config)

    def backtest_exit(self, pos, date, row, history, bt_config):
        return None  # use shared exit logic


PLUGIN = EpisodicPivotPlugin()

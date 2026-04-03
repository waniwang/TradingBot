"""Breakout strategy plugin."""

from __future__ import annotations

from core.loader import ScheduleEntry, ExitAction, BacktestEntryResult
from strategies.breakout.scanner_nightly import nightly_scan_job


class BreakoutPlugin:
    """
    Breakout — long setup on consolidation breakouts.

    Two-phase scan:
    - Nightly (5 PM): universe ranking + consolidation analysis -> watching/ready
    - Premarket (6 AM): promote ready -> active

    Multi-day candidates: demote back to ready at EOD (not expired).
    Uses shared exit logic (stop, partial, trailing MA close).
    """

    name = "breakout"
    display_name = "Breakout"
    watchlist_persist_days = 0  # multi-day: demote to ready at EOD

    # Extra cron job: nightly scan at 5 PM ET
    schedule = [
        ScheduleEntry(
            job_id="breakout_nightly_scan",
            cron={"hour": 17, "minute": 0, "day_of_week": "mon-fri"},
            handler=nightly_scan_job,
        ),
    ]

    def premarket_scan(self, config, client, db_engine, notify) -> list[dict]:
        from strategies.breakout.scanner_premarket import promote_ready_candidates
        from datetime import datetime
        import pytz

        ET = pytz.timezone("America/New_York")
        today = datetime.now(ET).date()
        return promote_ready_candidates(db_engine, today)

    def evaluate_signal(self, ticker, watchlist_entry, **ctx):
        from strategies.breakout.signal import check_breakout

        cfg = ctx.get("config", {}).get("strategies", {}).get("breakout", {})
        return check_breakout(
            ticker,
            candles_1m=ctx["candles_1m"],
            daily_closes=ctx["daily_closes"],
            daily_volumes=ctx["daily_volumes"],
            current_price=ctx["current_price"],
            current_volume=ctx["current_volume"],
            config=cfg,
            daily_lows=ctx.get("daily_lows"),
            daily_highs=ctx.get("daily_highs"),
            minutes_since_open=ctx.get("minutes_since_open"),
        )

    def on_position_update(self, pos, current_price, daily_closes) -> ExitAction | None:
        return None  # use shared exit logic

    def backtest_entry(self, ticker, date, row, history, bt_config) -> BacktestEntryResult | None:
        from strategies.breakout.backtest import check_entry

        return check_entry(ticker, date, row, history, bt_config)

    def backtest_exit(self, pos, date, row, history, bt_config):
        return None  # use shared exit logic


PLUGIN = BreakoutPlugin()

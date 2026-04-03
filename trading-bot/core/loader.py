"""
Strategy plugin discovery and registry.

Each strategy is a Python package under strategies/<name>/ that exposes
a module-level PLUGIN instance satisfying the StrategyPlugin protocol.

Usage:
    plugins = load_strategies(["episodic_pivot", "breakout"])
    plugin = get_plugin("episodic_pivot")
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"


# ---------------------------------------------------------------------------
# Data types returned by plugin hooks
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    """One cron job that a strategy wants to register."""

    job_id: str           # e.g. "breakout_nightly_scan"
    cron: dict            # APScheduler CronTrigger kwargs: {"hour": 17, "minute": 0}
    handler: Callable     # callable(config, client, db_engine, notify)


@dataclass
class ExitAction:
    """Returned by on_position_update when a strategy-specific exit fires."""

    action: str           # "partial" | "close"
    reason: str           # e.g. "parabolic_target"


@dataclass
class BacktestEntryResult:
    """Returned by backtest_entry when a simulated entry fires."""

    entry_price: float
    stop_price: float
    side: str             # "long" | "short"


# ---------------------------------------------------------------------------
# Strategy plugin protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class StrategyPlugin(Protocol):
    """
    Duck-typed protocol every strategy plugin must satisfy.

    A strategy is a folder under strategies/ with an __init__.py that
    exposes a module-level PLUGIN instance implementing this protocol.
    """

    # --- Identity ---
    name: str                      # matches folder name and DB setup_type
    display_name: str              # human-readable, for logs and Telegram
    watchlist_persist_days: int    # 1 = single-day (expire EOD), 0 = multi-day (demote to ready)

    # --- Schedule ---
    schedule: list[ScheduleEntry]  # extra cron jobs (empty = none)

    # --- Core pipeline ---
    def premarket_scan(
        self,
        config: dict,
        client: object,
        db_engine: object,
        notify: Callable[[str], None],
    ) -> list[dict]:
        """
        Run premarket scan. Return candidates as list[dict] with at
        minimum {"ticker": str}. Orchestrator persists them to Watchlist.
        """
        ...

    def evaluate_signal(
        self,
        ticker: str,
        watchlist_entry: dict,
        **ctx,
    ) -> object | None:
        """
        Called for each 1m bar on a watchlist ticker.
        Returns SignalResult | None.
        """
        ...

    # --- Optional hooks (default no-ops) ---
    def on_position_update(
        self,
        pos: object,
        current_price: float,
        daily_closes: list[float],
    ) -> ExitAction | None:
        """
        Strategy-specific intraday exit logic.
        Return ExitAction to handle the exit, or None to fall through
        to shared stop/partial/trailing logic.
        """
        ...

    def backtest_entry(
        self,
        ticker: str,
        date: str,
        row: object,
        history: dict,
        bt_config: object,
    ) -> BacktestEntryResult | None:
        """Return an entry if simulated signal fires, else None."""
        ...

    def backtest_exit(
        self,
        pos: object,
        date: str,
        row: object,
        history: dict,
        bt_config: object,
    ) -> tuple[float, str] | None:
        """
        Strategy-specific backtest exit.
        Return (exit_price, reason) or None for shared exit logic.
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, StrategyPlugin] = {}


def load_strategies(enabled: list[str]) -> dict[str, StrategyPlugin]:
    """
    Discover and load strategy plugins from the strategies/ directory.

    Only loads strategies whose name is in `enabled`.
    Returns {name: plugin_instance}.
    """
    global _registry
    _registry.clear()

    for strategy_dir in sorted(STRATEGIES_DIR.iterdir()):
        if not strategy_dir.is_dir() or strategy_dir.name.startswith("_"):
            continue
        name = strategy_dir.name
        if name not in enabled:
            logger.info("Strategy '%s' not in enabled list — skipping", name)
            continue
        try:
            module = importlib.import_module(f"strategies.{name}")
            plugin = module.PLUGIN
            _registry[name] = plugin
            logger.info("Loaded strategy plugin: %s (%s)", plugin.display_name, name)
        except Exception as e:
            logger.error("Failed to load strategy '%s': %s", name, e, exc_info=True)
            raise  # fail fast — a broken plugin is a deployment error

    return _registry


def get_registry() -> dict[str, StrategyPlugin]:
    """Return the loaded plugin registry."""
    return _registry


def get_plugin(setup_type: str) -> StrategyPlugin | None:
    """Look up a plugin by setup_type name."""
    return _registry.get(setup_type)

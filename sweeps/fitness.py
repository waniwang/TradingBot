"""Fitness gates for sweep results.

A 'gate' is a hard go/no-go threshold. Pareto ranking happens elsewhere — gates
just decide whether a combo is admissible. Designed so each gate function
has a clear name and reason string usable both as pytest assertion and
as plain-Python evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FitnessConfig:
    min_trades: int = 30
    min_profit_factor: float = 1.5
    min_annual_sharpe: float = 1.0
    max_drawdown_pct: float = 30.0  # max allowed magnitude (drawdown is negative; we compare |dd|)
    min_win_rate: float = 0.0  # disabled by default; enable per-strategy if needed

    @classmethod
    def from_yaml(cls, path: Path | str) -> "FitnessConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)


def evaluate(metrics: dict, cfg: FitnessConfig) -> tuple[bool, list[str]]:
    """Return (passed, list_of_failure_reasons). Empty failures = passed."""
    failures: list[str] = []

    if metrics.get("skipped"):
        return False, [f"skipped: {metrics.get('reason', 'unknown')}"]

    n = metrics.get("n", 0)
    if n < cfg.min_trades:
        failures.append(f"n={n} < min_trades={cfg.min_trades}")

    pf = metrics.get("profit_factor")
    if pf is None or pf < cfg.min_profit_factor:
        failures.append(f"profit_factor={pf} < {cfg.min_profit_factor}")

    asharpe = metrics.get("annual_sharpe", 0.0)
    if asharpe < cfg.min_annual_sharpe:
        failures.append(f"annual_sharpe={asharpe:.2f} < {cfg.min_annual_sharpe}")

    dd = metrics.get("max_dd_pct", 0.0)
    if abs(dd) > cfg.max_drawdown_pct:
        failures.append(f"max_dd={abs(dd):.1f}% > {cfg.max_drawdown_pct}%")

    wr = metrics.get("win_rate", 0.0)
    if wr < cfg.min_win_rate:
        failures.append(f"win_rate={wr:.1f}% < {cfg.min_win_rate}%")

    return (len(failures) == 0, failures)

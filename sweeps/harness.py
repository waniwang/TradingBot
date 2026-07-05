"""Shared backtest primitives for parameter sweeps.

Extracted from ep_optimize_5yr.py so harness, sweep runner, and fitness gates
all use the same filter/exit/metrics code. Keep this file pure: no I/O, no
side effects on import beyond what's strictly required.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

PERIOD_MAP = {
    "1D": "Second day change%",
    "10D": "10thD change%",
    "20D": "20thD change%",
    "50D": "50thD change%",
}
CHECKPOINTS = ["Second day change%", "10thD change%", "20thD change%", "50thD change%"]
HOLD_DAYS_MAP = {"1D": 1, "10D": 10, "20D": 20, "50D": 50}


def derive_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the derived columns the filters expect. Idempotent."""
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df["atr_pct"] = df["10 Day ATR"] / df["Close"] * 100
    df["downside_from_open"] = (df["Open"] - df["Low"]) / df["Open"] * 100
    hl = df["High"] - df["Low"]
    df["close_in_range"] = np.where(hl > 0, (df["Close"] - df["Low"]) / hl * 100, 50.0)
    df["mktcap_b"] = df["Market Cap"] / 1e9
    df["ddv"] = df["Close"] * df["Volume"] / 1e6
    df["year"] = df["Date"].dt.year
    df["close_to_low_pct"] = np.where(
        df["Close"] > 0,
        (df["Close"] - df["Low"]) / df["Close"] * 100,
        df["atr_pct"] * 1.5,
    )
    return df


@lru_cache(maxsize=4)
def load_data(path: str) -> pd.DataFrame:
    """Load Excel/CSV and derive columns. Cached per-process so ProcessPool
    workers pay the load cost once and amortize across all combos they own."""
    if str(path).endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    return derive_columns(df)


def apply_filters(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    mask = (
        (df["CHG-OPEN%"] > cfg.get("chg_open_min", 0.0))
        & (df["CHG-OPEN%"] <= cfg.get("chg_open_max", 1e9))
        & (df["close_in_range"] >= cfg.get("close_in_range_min", 0.0))
        & (df["close_in_range"] <= cfg.get("close_in_range_max", 100.0))
        & (df["downside_from_open"] < cfg.get("downside_from_open_max", 100.0))
        & (df["Prev 10D change%"] >= cfg.get("prev_10d_min", -9999.0))
        & (df["Prev 10D change%"] <= cfg.get("prev_10d_max", 9999.0))
        & (df["atr_pct"] >= cfg.get("atr_pct_min", 0.0))
        & (df["atr_pct"] <= cfg.get("atr_pct_max", 100.0))
        & (df["mktcap_b"] >= cfg.get("mktcap_b_min", 0.0))
        & (df["mktcap_b"] <= cfg.get("mktcap_b_max", 9999.0))
        & (df["Volume"] <= cfg.get("volume_max", 1e15))
        & (df["ddv"] >= cfg.get("ddv_min", 0.0))
    )
    # EP-2.0 context columns — only present in ep_events_combined.csv;
    # filters on them are no-ops for the legacy per-catalyst datasets.
    if "prior_gaps_90d" in df.columns:
        mask &= df["prior_gaps_90d"] >= cfg.get("prior_gaps_90d_min", 0)
    if "is_earnings" in df.columns and cfg.get("catalyst") in ("earnings", "news"):
        col = "is_earnings" if cfg["catalyst"] == "earnings" else "is_news"
        mask &= df[col] == 1
    return df[mask].copy()


def run_simple(trades: pd.DataFrame, stop_pct: float, hold_period: str) -> pd.DataFrame:
    hold_col = PERIOD_MAP[hold_period]
    hold_idx = CHECKPOINTS.index(hold_col)
    rows = []
    for _, t in trades.iterrows():
        stopped = False
        exit_return = None
        for i, cp in enumerate(CHECKPOINTS):
            if i > hold_idx:
                break
            ret = t[cp]
            if pd.isna(ret):
                continue
            if ret <= -stop_pct:
                exit_return = -stop_pct
                stopped = True
                break
        if not stopped:
            exit_return = t[hold_col] if pd.notna(t[hold_col]) else None
        if exit_return is None:
            continue
        rows.append({"Date": t["Date"], "Return%": exit_return, "Stopped": stopped})
    return pd.DataFrame(rows)


def run_scale_out(
    trades: pd.DataFrame,
    atr_stop_mult: float,
    t1_target: float,
    t1_pct: float,
    t2_target: float,
    t2_pct: float,
    hold_period: str,
) -> pd.DataFrame:
    hold_col = PERIOD_MAP[hold_period]
    hold_idx = CHECKPOINTS.index(hold_col)
    rows = []
    for _, t in trades.iterrows():
        atr_stop = t["atr_pct"] * atr_stop_mult
        gap_low_pct = t["close_to_low_pct"]
        stop_pct = min(atr_stop, gap_low_pct) if gap_low_pct > 0 else atr_stop

        remaining = 1.0
        exits: list[tuple[float, float]] = []
        t1_hit = t2_hit = stopped = False

        for i, cp in enumerate(CHECKPOINTS):
            if i > hold_idx:
                break
            ret = t[cp]
            if pd.isna(ret):
                continue
            if ret <= -stop_pct:
                exits.append((remaining, -stop_pct))
                remaining = 0
                stopped = True
                break
            if not t1_hit and ret >= t1_target:
                exits.append((t1_pct, t1_target))
                remaining -= t1_pct
                t1_hit = True
            if not t2_hit and ret >= t2_target:
                exits.append((t2_pct, t2_target))
                remaining -= t2_pct
                t2_hit = True

        if remaining > 0 and not stopped:
            final_ret = t[hold_col]
            if pd.notna(final_ret):
                exits.append((remaining, final_ret))

        if not exits:
            continue

        total_w = sum(w for w, _ in exits)
        blended = sum(w * r for w, r in exits) / total_w
        rows.append({"Date": t["Date"], "Return%": blended, "Stopped": stopped})

    return pd.DataFrame(rows)


def compute_metrics(out: pd.DataFrame, hold_period: str | None = None) -> dict | None:
    """Per-trade and equity-curve metrics. `out` must have Date, Return%, Stopped.

    sharpe is per-trade (mean/std). annual_sharpe approximates by sqrt(trades/year)
    using actual elapsed years in the trade dates — only valid when len(out) >= 2.
    max_dd_pct is on the date-sorted compound equity curve, ASSUMING 1 unit of
    capital per trade (i.e. all-in each entry, sequential). This overstates
    real drawdown for any portfolio that holds positions in parallel — interpret
    accordingly when comparing to live account drawdown.
    """
    n = len(out)
    if n == 0:
        return None

    out = out.sort_values("Date").reset_index(drop=True)
    returns = out["Return%"].astype(float)

    winners = out[out["Return%"] > 0]
    losers = out[out["Return%"] <= 0]
    stopped = out[out["Stopped"] == True]  # noqa: E712 — explicit bool match
    pf = (
        abs(winners["Return%"].sum() / losers["Return%"].sum())
        if len(winners) and len(losers)
        else None
    )

    mean = returns.mean()
    std = returns.std(ddof=1) if n >= 2 else 0.0
    sharpe = mean / std if std > 0 else 0.0

    # Approximate annualized Sharpe from actual trade-date span.
    annual_sharpe = 0.0
    if n >= 2 and std > 0:
        span_days = (out["Date"].iloc[-1] - out["Date"].iloc[0]).days
        if span_days > 0:
            trades_per_year = n / (span_days / 365.25)
            annual_sharpe = sharpe * np.sqrt(trades_per_year)

    equity = (1 + returns / 100).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_dd_pct = float(drawdown.min())

    total_return_pct = float((equity.iloc[-1] - 1) * 100)

    return {
        "n": n,
        "win_rate": len(winners) / n * 100,
        "avg": float(mean),
        "median": float(returns.median()),
        "stop_rate": len(stopped) / n * 100,
        "avg_win": float(winners["Return%"].mean()) if len(winners) else None,
        "avg_loss": float(losers["Return%"].mean()) if len(losers) else None,
        "profit_factor": float(pf) if pf is not None else None,
        "sharpe": float(sharpe),
        "annual_sharpe": float(annual_sharpe),
        "max_dd_pct": max_dd_pct,
        "total_return_pct": total_return_pct,
        "score": float(mean * (len(winners) / n) * pf) if pf else 0.0,
    }


@dataclass(frozen=True)
class BacktestRequest:
    """One combo to run. Frozen so it's hashable + ProcessPool-safe."""
    strategy_name: str
    mode: str  # "simple" or "scale_out"
    params: tuple[tuple[str, object], ...]  # dict converted to sorted tuple of pairs
    data_path: str

    @property
    def params_dict(self) -> dict:
        return dict(self.params)


def run_backtest(req: BacktestRequest) -> dict:
    """Pure function: takes a BacktestRequest, returns a result dict.

    Result includes the input params (so the leaderboard can reconstruct config)
    plus all metrics. Returns {"skipped": True, "reason": ...} on min-trades fail
    so the caller can count skips without losing the combo identity.
    """
    df = load_data(req.data_path)
    p = req.params_dict

    cfg = {
        "chg_open_min": p.get("chg_open_min", 0.0),
        "close_in_range_min": p.get("close_in_range_min", 0.0),
        "downside_from_open_max": p.get("downside_from_open_max", 100.0),
        "prev_10d_min": p.get("prev_10d_min", -9999.0),
        "prev_10d_max": p.get("prev_10d_max", 9999.0),
        "atr_pct_min": p.get("atr_pct_min", 0.0),
        "atr_pct_max": p.get("atr_pct_max", 100.0),
        "mktcap_b_max": p.get("mktcap_b_max", 9999.0),
        "ddv_min": p.get("ddv_min", 0.0),
    }
    trades = apply_filters(df, cfg)

    base = {"strategy_name": req.strategy_name, "mode": req.mode, **p}

    if len(trades) < 1:
        return {**base, "skipped": True, "reason": "no_trades_after_filter", "n": 0}

    if req.mode == "simple":
        out = run_simple(trades, p["stop_pct"], p.get("hold_period", "50D"))
    elif req.mode == "scale_out":
        out = run_scale_out(
            trades,
            p.get("atr_stop_mult", 1.5),
            p.get("t1_target", 8.0),
            p.get("t1_pct", 0.40),
            p.get("t2_target", 15.0),
            p.get("t2_pct", 0.40),
            p.get("hold_period", "50D"),
        )
    else:
        raise ValueError(f"unknown mode: {req.mode}")

    metrics = compute_metrics(out, hold_period=p.get("hold_period", "50D"))
    if metrics is None:
        return {**base, "skipped": True, "reason": "no_results", "n": 0}

    return {**base, "skipped": False, **metrics}


def find_data_root() -> Path:
    """Resolve the TradingBot data directory regardless of CWD."""
    here = Path(__file__).resolve().parent.parent
    p = here / "market data download"
    if not p.exists():
        raise FileNotFoundError(f"data dir not found: {p}")
    return p

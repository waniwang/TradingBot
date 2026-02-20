#!/usr/bin/env python3
"""
Run a backtest on historical data.

Usage:
    python run_backtest.py                    # default: 20 liquid stocks, 2022-2024
    python run_backtest.py --tickers AAPL MSFT NVDA --start 2023-01-01 --end 2024-12-31
    python run_backtest.py --sp500 --start 2022-01-01 --end 2024-12-31
    python run_backtest.py --setup breakout   # test only breakout strategy
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from backtest.data import fetch_historical_bars, get_sp500_tickers
from backtest.runner import BacktestRunner, BacktestConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("run_backtest")

# Liquid, well-known stocks good for initial testing
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "AMD", "NFLX", "CRM",
    "AVGO", "ORCL", "SHOP", "SQ", "SNOW",
    "PLTR", "COIN", "MARA", "SMCI", "ARM",
]


def print_metrics(metrics: dict, label: str = ""):
    """Pretty-print backtest metrics."""
    header = f"=== BACKTEST RESULTS{' — ' + label if label else ''} ==="
    print("\n" + header)
    print("=" * len(header))
    print(f"  Total trades:       {metrics['total_trades']}")
    print(f"  Win rate:           {metrics['win_rate']:.1f}%")
    print(f"  Avg winner:         ${metrics['avg_winner']:,.2f}")
    print(f"  Avg loser:          ${metrics['avg_loser']:,.2f}")
    print(f"  W/L ratio:          {metrics['wl_ratio']:.2f}")
    print(f"  Profit factor:      {metrics['profit_factor']:.2f}")
    print(f"  Sharpe ratio:       {metrics['sharpe']:.2f}")
    print(f"  Max drawdown:       {metrics['max_drawdown_pct']:.1f}%")
    print(f"  Total return:       {metrics['total_return_pct']:.1f}%")
    print(f"  CAGR:               {metrics['cagr']:.1f}%")
    print(f"  Calmar ratio:       {metrics.get('calmar', 0):.2f}")
    print(f"  Avg days held:      {metrics.get('avg_days_held', 0):.1f}")
    print(f"  Max consec losses:  {metrics.get('max_consecutive_losses', 0)}")
    print(f"  Trades/month:       {metrics.get('avg_trades_per_month', 0):.1f}")
    print()


def print_trade_log(trades, max_rows: int = 30):
    """Print a sample of trades."""
    if not trades:
        print("  No trades executed.\n")
        return
    print(f"  Trade log ({len(trades)} total, showing first {min(len(trades), max_rows)}):")
    print(f"  {'Ticker':<8} {'Setup':<18} {'Side':<6} {'Entry':<12} {'Exit':<12} "
          f"{'Entry$':>8} {'Exit$':>8} {'P&L':>10} {'Reason':<18}")
    print("  " + "-" * 110)
    for t in trades[:max_rows]:
        sign = "+" if t.pnl >= 0 else ""
        print(f"  {t.ticker:<8} {t.setup_type:<18} {t.side:<6} {t.entry_date:<12} {t.exit_date:<12} "
              f"{t.entry_price:>8.2f} {t.exit_price:>8.2f} {sign + f'${t.pnl:,.2f}':>10} {t.exit_reason:<18}")
    if len(trades) > max_rows:
        print(f"  ... and {len(trades) - max_rows} more trades")
    print()


def print_setup_breakdown(trades):
    """Print metrics broken down by setup type."""
    from collections import defaultdict
    by_setup = defaultdict(list)
    for t in trades:
        by_setup[t.setup_type].append(t)

    if not by_setup:
        return

    print("=== BREAKDOWN BY SETUP ===")
    for setup, setup_trades in sorted(by_setup.items()):
        winners = [t for t in setup_trades if t.pnl > 0]
        losers = [t for t in setup_trades if t.pnl < 0]
        total_pnl = sum(t.pnl for t in setup_trades)
        win_rate = len(winners) / len(setup_trades) * 100 if setup_trades else 0
        print(f"  {setup}: {len(setup_trades)} trades, "
              f"win rate {win_rate:.0f}%, "
              f"total P&L ${total_pnl:+,.2f}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Run trading backtest")
    parser.add_argument("--tickers", nargs="+", default=None,
                        help="List of tickers to test")
    parser.add_argument("--sp500", action="store_true",
                        help="Use S&P 500 universe (slow, ~500 tickers)")
    parser.add_argument("--start", default="2022-01-01",
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31",
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--setup", choices=["breakout", "episodic_pivot", "parabolic_short"],
                        default=None, help="Test only one setup type")
    parser.add_argument("--capital", type=float, default=100_000,
                        help="Starting capital (default: $100,000)")
    parser.add_argument("--max-positions", type=int, default=4,
                        help="Max concurrent positions (default: 4)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine tickers
    if args.sp500:
        logger.info("Fetching S&P 500 tickers...")
        tickers = get_sp500_tickers()
        if not tickers:
            logger.error("Could not fetch S&P 500 tickers")
            sys.exit(1)
        logger.info("Got %d S&P 500 tickers", len(tickers))
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        tickers = DEFAULT_TICKERS

    setups = [args.setup] if args.setup else None

    # Download data
    logger.info("Downloading data for %d tickers: %s — %s", len(tickers), args.start, args.end)
    t0 = time.time()
    bars = fetch_historical_bars(tickers, args.start, args.end)
    dl_time = time.time() - t0
    logger.info("Data download: %.1fs (%d tickers loaded)", dl_time, len(bars))

    if not bars:
        logger.error("No data downloaded — cannot run backtest")
        sys.exit(1)

    # Show a sample of what we got
    sample_ticker = list(bars.keys())[0]
    sample_df = bars[sample_ticker]
    logger.info("Sample: %s has %d bars from %s to %s",
                sample_ticker, len(sample_df),
                sample_df["date"].iloc[0] if "date" in sample_df.columns else "?",
                sample_df["date"].iloc[-1] if "date" in sample_df.columns else "?")

    # Configure and run
    config = BacktestConfig(
        initial_capital=args.capital,
        max_positions=args.max_positions,
    )

    logger.info("Running backtest (setups=%s, capital=$%s, max_pos=%d)...",
                setups or "all", f"{args.capital:,.0f}", args.max_positions)
    t0 = time.time()
    runner = BacktestRunner(config)
    metrics = runner.run(bars, setups=setups)
    run_time = time.time() - t0
    logger.info("Backtest completed in %.1fs", run_time)

    # Print results
    label = args.setup or "all setups"
    label += f" | {len(bars)} tickers | {args.start} to {args.end}"
    print_metrics(metrics, label)
    print_trade_log(runner.trades)
    print_setup_breakdown(runner.trades)

    # Equity curve summary
    if runner.daily_equity:
        print("=== EQUITY CURVE ===")
        eq = runner.daily_equity
        print(f"  Start:   ${eq[0]:>12,.2f}")
        print(f"  End:     ${eq[-1]:>12,.2f}")
        print(f"  Peak:    ${max(eq):>12,.2f}")
        print(f"  Trough:  ${min(eq):>12,.2f}")
        print(f"  Days:    {len(eq)}")
        print()

    # Target check
    print("=== TARGET CHECK ===")
    targets = {
        "Win rate > 45%": metrics["win_rate"] > 45,
        "W/L ratio > 3x": metrics["wl_ratio"] > 3,
        "Sharpe > 1.0": metrics["sharpe"] > 1.0,
        "Max DD < 20%": metrics["max_drawdown_pct"] < 20,
        "Profit factor > 2.0": metrics["profit_factor"] > 2.0,
    }
    for name, passed in targets.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
    print()


if __name__ == "__main__":
    main()

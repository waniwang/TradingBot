#!/usr/bin/env python3
"""
Run EP swing strategy backtests on historical spreadsheet data.

Usage:
    python run_ep_backtest.py --type earnings                    # both A and B
    python run_ep_backtest.py --type news                        # both A and B
    python run_ep_backtest.py --type earnings --strategy A       # single strategy
    python run_ep_backtest.py --type earnings --data /path.xlsx  # custom data
    python run_ep_backtest.py --type earnings --year 2025        # single year
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("run_ep_backtest")


def print_ep_stats(stats: dict, config: dict, label: str = ""):
    """Print EP-specific percentage-based metrics."""
    header = f"=== {label} ==="
    print(f"\n{header}")
    print("=" * len(header))
    print(f"  Data file:          {config['data_file']}")
    print(f"  Total candidates:   {config['total_candidates']}")
    print(f"  Strategy:           {config['variant']}")
    print(f"  Stop:               -{config['stop_pct']}% | Hold: {config['hold_period']}")
    print(f"  ---")
    print(f"  Trades:             {stats['n']}")
    print(f"  Win rate:           {stats['win_rate']:.0f}%")
    print(f"  Avg return:         {stats['avg_return']:+.2f}%")
    print(f"  Median return:      {stats['med_return']:+.2f}%")
    print(f"  Stopped out:        {stats['stop_rate']:.0f}%")
    if stats.get("avg_winner"):
        print(f"  Avg winner:         {stats['avg_winner']:+.2f}%")
    if stats.get("avg_loser"):
        print(f"  Avg loser:          {stats['avg_loser']:+.2f}%")
    if stats.get("best") is not None:
        print(f"  Best trade:         {stats['best']:+.2f}%")
    if stats.get("worst") is not None:
        print(f"  Worst trade:        {stats['worst']:+.2f}%")
    print(f"  Profit factor:      {stats['pf']:.2f}")
    print()


def print_trade_log(trades, max_rows: int = 30):
    """Print a sample of trades."""
    if not trades:
        print("  No trades executed.\n")
        return
    print(f"  Trade log ({len(trades)} total, showing first {min(len(trades), max_rows)}):")
    print(f"  {'Ticker':<8} {'Date':<12} {'Entry$':>8} {'Exit$':>8} {'Return%':>9} {'Reason':<18}")
    print("  " + "-" * 65)
    for t in trades[:max_rows]:
        ret = (t.exit_price - t.entry_price) / t.entry_price * 100
        sign = "+" if ret >= 0 else ""
        print(f"  {t.ticker:<8} {t.entry_date:<12} {t.entry_price:>8.2f} {t.exit_price:>8.2f} "
              f"{sign}{ret:>7.2f}% {t.exit_reason:<18}")
    if len(trades) > max_rows:
        print(f"  ... and {len(trades) - max_rows} more trades")
    print()


def print_yearly_breakdown(yearly: dict):
    """Print year-by-year stats table."""
    if not yearly:
        return
    print("  Year-by-Year Breakdown:")
    print(f"  {'Year':<6} {'Trades':>7} {'Win%':>6} {'Avg Ret%':>9} {'Med Ret%':>9}")
    print("  " + "-" * 40)
    for year, s in sorted(yearly.items()):
        print(f"  {year:<6} {s['n']:>7} {s['win_rate']:>5.0f}% {s['avg_return']:>+8.2f}% {s['med_return']:>+8.2f}%")
    print()


def main():
    parser = argparse.ArgumentParser(description="Run EP swing strategy backtest")
    parser.add_argument("--type", choices=["earnings", "news"], required=True,
                        help="Strategy type: earnings or news")
    parser.add_argument("--strategy", choices=["A", "B", "all"], default="all",
                        help="Strategy variant (default: all)")
    parser.add_argument("--data", default=None,
                        help="Path to Excel data file (uses default if omitted)")
    parser.add_argument("--year", type=int, default=None,
                        help="Filter to a single year (e.g., 2025)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show debug logging")
    parser.add_argument("--trades", action="store_true",
                        help="Show individual trade log")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Import the right strategy backtest module
    if args.type == "earnings":
        from strategies.ep_earnings.backtest import run_backtest
    else:
        from strategies.ep_news.backtest import run_backtest

    # Run backtest
    results = run_backtest(
        data_path=args.data,
        strategy=args.strategy,
        year=args.year,
    )

    # Print results for each variant
    type_label = "EP Earnings" if args.type == "earnings" else "EP News"
    year_label = f" ({args.year})" if args.year else " (2020-2025)"

    for variant, data in sorted(results.items()):
        label = f"{type_label} Strategy {variant}{year_label}"
        print_ep_stats(data["stats"], data["config"], label)
        if args.trades:
            print_trade_log(data["trades"])
        print_yearly_breakdown(data["yearly"])

    # Side-by-side comparison if both A and B
    if len(results) == 2:
        print(f"\n{'=' * 60}")
        print(f"  {type_label} — STRATEGY COMPARISON{year_label}")
        print(f"{'=' * 60}")
        print(f"  {'Metric':<22} {'Strategy A':>15} {'Strategy B':>15}")
        print(f"  {'-' * 52}")

        a = results["A"]["stats"]
        b = results["B"]["stats"]
        rows = [
            ("Trades", f"{a['n']}", f"{b['n']}"),
            ("Win Rate", f"{a['win_rate']:.0f}%", f"{b['win_rate']:.0f}%"),
            ("Avg Return", f"{a['avg_return']:+.2f}%", f"{b['avg_return']:+.2f}%"),
            ("Median Return", f"{a['med_return']:+.2f}%", f"{b['med_return']:+.2f}%"),
            ("Stopped Out", f"{a['stop_rate']:.0f}%", f"{b['stop_rate']:.0f}%"),
            ("Avg Winner", f"{a['avg_winner']:+.2f}%", f"{b['avg_winner']:+.2f}%"),
            ("Avg Loser", f"{a['avg_loser']:+.2f}%", f"{b['avg_loser']:+.2f}%"),
            ("Profit Factor", f"{a['pf']:.2f}", f"{b['pf']:.2f}"),
        ]
        for label, va, vb in rows:
            print(f"  {label:<22} {va:>15} {vb:>15}")
        print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()

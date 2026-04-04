# EP News Strategy

EOD long swing on news-driven (non-earnings) gap-ups. Same timing as EP Earnings but excludes earnings events.

## Flow

1. **3:00 PM** — `scanner.py` finds news gappers (excludes earnings), `strategy.py` evaluates A/B filters
2. **3:50 PM** — Plugin executes entries near close
3. **Ongoing** — Stop per strategy tier, max 50-day hold

## Scanner Filters (`scanner.py`)

Same three-phase filter as EP Earnings, with differences:

| Difference | EP Earnings | EP News |
|------------|------------|---------|
| Market cap | >= $800M | >= $1B |
| Earnings | Required | Excluded |

## Strategy A (NEWS-Tight) — stop -7%

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | >= 50% |
| Downside from open | < 3% |
| Prev 10D change | <= -20% |
| ATR% | between 3% and 7% |
| Volume | < 3M shares |

## Strategy B (NEWS-Relaxed) — stop -10%

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | between 30% and 80% |
| Downside from open | < 6% |
| Prev 10D change | <= -10% |
| ATR% | between 3% and 7% |
| Volume | < 5M shares |

If both pass, Strategy A is used (tighter stop).

## Backtesting

Uses spreadsheet-based backtest with pre-computed gap-day features and forward return checkpoints.

**Data source:** `backtest/data/2020-2025 EP Selection NEWS V2.xlsx` — 4,714 news gap candidates (2020-2025).

**How to run:**

```bash
cd trading-bot
.venv/bin/python run_ep_backtest.py --type news              # both A and B
.venv/bin/python run_ep_backtest.py --type news --strategy A  # single strategy
.venv/bin/python run_ep_backtest.py --type news --year 2025   # single year
.venv/bin/python run_ep_backtest.py --type news --trades      # show trade log
```

**Methodology:**
- Same as EP earnings backtest (see `strategies/ep_earnings/README.md`)
- Strategy A uses -7% stop; Strategy B uses -10% stop (per `config.yaml`)
- Known limitation: checkpoint stops miss intra-period dips (slightly optimistic)

**Results (2020-2025, 4,714 candidates):**

| Metric | Strategy A | Strategy B |
|--------|-----------|-----------|
| Trades | 48 | 137 |
| Win Rate | 67% | 62% |
| Avg Return | +21.26% | +17.28% |
| Profit Factor | 10.82 | 6.17 |
| Best Year | 2020 (75% WR) | 2020 (78% WR) |
| Worst Year | 2021 (50% WR) | 2021 (29% WR) |

Strategy A has very tight filters (48 trades over 6 years) but exceptional quality. Strategy B offers more trades with strong returns.

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 1_000_000_000
exclude_earnings: true
max_hold_days: 50
```

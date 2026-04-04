# EP Earnings Strategy

EOD long swing on earnings gap-ups. Scans at 3 PM, enters near close at 3:50 PM. Holds up to 50 days.

## Flow

1. **3:00 PM** — `scanner.py` finds earnings gappers, `strategy.py` evaluates A/B filters
2. **3:50 PM** — Plugin executes entries (limit orders near close)
3. **Ongoing** — -7% stop, max 50-day hold, shared exit logic (partial exits, trailing MA)

## Scanner Filters (`scanner.py`)

Three-phase filter:

| Phase | Filter | Threshold |
|-------|--------|-----------|
| A (Alpaca) | Price | > $3 (prev close) |
| A | Gap % | >= 8% |
| A | Open | > prev day's high |
| B (yfinance) | Open | > 200-day SMA |
| B | RVOL | >= 1.0x (14d avg) |
| B | Prior 6mo gain | < 50% |
| C (per-ticker) | Market cap | >= $800M |
| C | Earnings | Must have earnings on gap day |

## Strategy A (Tight) — 69% WR, PF 5.68

| Filter | Value |
|--------|-------|
| CHG-OPEN% | > 0 (positive intraday) |
| Close in range | >= 50% (top half) |
| Downside from open | < 3% |
| Prev 10D change | between -30% and -10% |
| Stop | -7% |

## Strategy B (Relaxed) — 61% WR, PF 5.62

| Filter | Value |
|--------|-------|
| CHG-OPEN% | > 0 |
| Close in range | >= 50% |
| ATR% | between 2% and 5% |
| Prev 10D change | < -10% |
| Stop | -7% |

## Kill Zones (Avoid)

- Prev 10D > 0% (ran up into earnings): 31% WR, -7.4% mean
- CHG-OPEN% < 0 AND close_in_range < 50: 40% WR

## Backtesting

Uses spreadsheet-based backtest with pre-computed gap-day features and forward return checkpoints.

**Data source:** `backtest/data/2020-2025 EP Selection EARNINGS.xlsx` — 907 earnings gap candidates (2020-2025).

**How to run:**

```bash
cd trading-bot
.venv/bin/python run_ep_backtest.py --type earnings              # both A and B
.venv/bin/python run_ep_backtest.py --type earnings --strategy A  # single strategy
.venv/bin/python run_ep_backtest.py --type earnings --year 2025   # single year
.venv/bin/python run_ep_backtest.py --type earnings --trades      # show trade log
```

**Methodology:**
- Loads Excel file with gap-day features (OHLCV, ATR, CHG-OPEN%, Prev 10D, etc.)
- Computes derived features (atr_pct, close_in_range, downside_from_open)
- Applies Strategy A/B filters as vectorized pandas masks
- Simulates exits using forward return checkpoints (1D, 10D, 20D, 50D): if any checkpoint breaches -7% stop, exits at stop; otherwise exits at 50D return
- Known limitation: checkpoint stops miss intra-period dips (slightly optimistic)

**Results (2020-2025, 907 candidates):**

| Metric | Strategy A | Strategy B |
|--------|-----------|-----------|
| Trades | 188 | 262 |
| Win Rate | 48% | 50% |
| Avg Return | +5.34% | +8.19% |
| Profit Factor | 2.64 | 3.54 |
| Best Year | 2025 (65% WR) | 2020 (62% WR) |
| Worst Year | 2021 (29% WR) | 2021 (30% WR) |

**Results (2025 only, 267 candidates):**

| Metric | Strategy A | Strategy B |
|--------|-----------|-----------|
| Trades | 48 | 82 |
| Win Rate | 65% | 59% |
| Avg Return | +8.17% | +11.07% |
| Profit Factor | 4.59 | 5.10 |

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 800_000_000
stop_loss_pct: 7.0
max_hold_days: 50
```

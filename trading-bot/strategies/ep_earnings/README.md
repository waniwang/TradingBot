# EP Earnings Strategy

EOD long swing on earnings gap-ups. Scans at 3 PM, enters near close at 3:50 PM. Holds up to 50 days.

## Flow

1. **3:00 PM** — `scanner.py` finds earnings gappers, `strategy.py` evaluates A/B/C filters. A/B entries are persisted as `Watchlist(stage="ready")` with the full execution payload in `metadata_json`. Strategy C candidates are persisted as `Watchlist(stage="watching")` with `meta.day2_confirm=true`.
2. **3:45 PM** — `job_day2_confirm` snapshots prices for yesterday's `watching` C rows. Confirmed (price > gap-day close) → flips to `stage="ready"` and writes `entry_price`/`stop_price`/`day1_return_pct` into `meta`. Rejected → `stage="expired"`.
3. **3:50 PM** — `job_execute` queries `Watchlist.stage="ready"` for `setup_type="ep_earnings"` and places orders. **DB-driven** — nothing is held in memory between scan/confirm and execute, so a process restart is safe.
4. **Ongoing** — -7% stop, max hold (50d for A/B, 20d for C), shared exit logic.

### Watchlist stage semantics

| Stage | Meaning |
|-------|---------|
| `watching` | Strategy C candidate awaiting day-2 confirmation |
| `ready` | Staged for the next 15:50 execution (A/B from today, or C confirmed from yesterday) |
| `triggered` | Order placed (set by `mark_triggered` after `_execute_entry` succeeds) |
| `expired` | Day-2 rejected, or EOD without trigger |

The same Watchlist row carries `setup_type="ep_earnings"` whether the eventual signal is A, B, or C — the strategy variant lives in `meta.ep_strategy`. When `_execute_entry` flips the row to `triggered`, plugins pass `watchlist_setup_type="ep_earnings"` so the C-flavored signal `setup_type="ep_earnings_c"` doesn't break the alignment.

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

## Strategy C (Bear Market / Day-2 Confirm) -- ~48% WR, PF ~3.3

Designed for bear market regimes where "strong gap day" filters (A/B) select stocks that get sold off hardest. Uses minimal filters + day-2 confirmation to filter out immediate reversals.

| Filter | Value |
|--------|-------|
| Prev 10D change | <= -10% (beaten down pre-earnings) |
| Day-2 confirm | 1D return > 0 (stock holds up next day) |
| Stop | -7% |
| Hold | 20 days (shorter than A/B's 50D) |

**Entry timing:** Scanned on gap day (3:00 PM), but NOT entered until day 2 (3:50 PM) after confirming positive 1D return. Entry price = day 2 close.

**Why it works:** In 2026 Q1 bear market, A/B went 0% WR while C showed 71% WR on 2026 data. The day-2 confirmation filters out stocks that gap up on earnings but immediately reverse.

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

## Dashboard Parameter Display

The Strategies detail page (`/strategies/ep_earnings`) renders each parameter with its description, variation (A/B/C), and the phase/job where it's applied (scan vs execute vs day-2 confirm). The descriptions and phase tags live in [`trading-bot/api/param_meta.py`](../../api/param_meta.py). Update that file whenever you add a new `ep_earnings_*` key to `config.yaml`.

The Variation column on the Trades tab and the A/B/C badges on the pipeline job-detail modal are derived at read time by joining `Signal.setup_type + ticker + fired_at` back to `Watchlist.meta["ep_strategy"]` — see [`api/variation.py`](../../api/variation.py).

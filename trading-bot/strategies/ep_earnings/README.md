# EP Earnings Strategy

EOD long swing on earnings gap-ups. Scans at 3 PM, enters near close at 3:50 PM. Holds up to 50 days.

## Flow

1. **3:00 PM** — `scanner.py` finds earnings gappers, `strategy.py` evaluates Strategy B filters. Passing entries are persisted as `Watchlist(stage="ready")` with the full execution payload in `metadata_json`.
2. **3:50–3:59 PM** — `job_execute` queries `Watchlist.stage="ready"` for `setup_type="ep_earnings"` and places orders. Fires every minute (10 attempts) so a briefly-down bot or a transient Alpaca error still gets a trade in before close. **DB-driven + idempotent** — nothing held in memory between scan and execute, and replays are blocked by the open-Position guard and the <10-minute non-terminal Order guard, so process restarts and multi-fire retries are both safe. **Price refresh**: each attempt calls `core.execution.resolve_execution_price` to fetch a live mid and pick entry/stop — if the live mid is ≤ scan price, scan wins; otherwise mid is used up to `ep_execute_max_price_bump_pct` above scan, with a `ep_execute_max_spread_pct` ceiling on bid-ask spread (wider quote → skip, retry next minute). Stop is always recomputed from the actual entry so the -7% rule holds.
3. **Ongoing exits** (in order of likely trigger):
   - **GTC -7% stop** at broker, fires on intraday low ≤ entry × 0.93
   - **9:40 AM ET D19+ partial** (added 2026-05-11): if at day 19+ and in profit, sell 40% market, move stop on remainder to entry × 1.05 (5% above entry, locks in min gain). Single-shot per position. See `monitor/position_tracker.py::check_ep_time_partial`.
   - **50-day max hold** at 3:55 PM EOD, forces close on remainder if reached
   - Reconcile drift detector catches any naked positions every 5 min (safety net)

### Watchlist stage semantics

| Stage | Meaning |
|-------|---------|
| `ready` | Staged for the next 15:50 execution |
| `triggered` | Order placed (set by `mark_triggered` after `_execute_entry` succeeds) |
| `expired` | EOD without trigger |

The Watchlist row carries `setup_type="ep_earnings"`; the strategy variant lives in `meta.ep_strategy` (currently always "B" — A and C were dropped 2026-05-08).

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

## Strategy B — 44.7% WR, PF 2.57, +4.95% avg (2020–2026 corrected)

| Filter | Value |
|--------|-------|
| CHG-OPEN% | > 0 |
| Close in range | >= 50% |
| ATR% (10D) | between 2% and 5% |
| Stop | -7% |
| Max hold | 50 calendar days |

## History

**2026-05-08: Strategies A and C dropped.** A re-validation against corrected 2020–2026 Spikeet data showed:

- **A-only trades** (passes A but not B) had PF 1.36, +1.40% avg over 68 trades — barely positive, and 4 of 7 years were losing years. The original "A is the tighter filter so prefer A" assumption was based on broken forward-return data; on clean data, A's distinctive `downside_from_open < 3%` filter selects worse trades than B's ATR range. Switching to B alone moves PF from 2.15 (production "A wins" effective) to 2.57.
- **Strategy C** (day-2 confirmation, 491 trades over 6.4yr) had PF 1.85 — *worse* than buying every gap (PF 1.95). The "stock holds up day 2" filter has near-zero predictive edge while contributing 76 trades/year of capital pressure. Dropped despite 0/7 losing years because the per-trade quality is too marginal vs the volume cost.

Open positions tagged `ep_earnings`, `ep_earnings_a`, or `ep_earnings_c` continue to be managed by `monitor/position_tracker.py` until they exit naturally (stop, partial, trailing MA, or 50d max hold).

**2026-04-21: Prev 10D change filter removed.** The Spikeet data column used to tune the P10D thresholds was sign-inverted vs yfinance. The 2026-05-08 corrected backtest confirmed the filter doesn't add edge on clean data either (PF 2.57 with no filter vs 2.74 with P10D<0, but trade count drops 57% — net total return drops materially).

## Error handling

Feature computation (`compute_features`, `_compute_atr_pct`) **raises** when daily bars are missing or short (< 11 rows). The evaluator catches the error per ticker and records it in `rejections` with `is_data_error=True`. The Telegram summary labels these distinctly (`DATA ERROR`) so the operator can tell a real filter miss apart from a yfinance fetch failure.

If *every* candidate in a scan fails with a data error, `job_scan` raises `RuntimeError` so `_track_job` fires `JOB FAILED` — a batch-wide yfinance outage is a bug, not silent "0 passed filters".

**Phase C earnings check:** `_check_earnings_today()` reads from `core/earnings.py::fetch_recent_earnings_dates` which **tries yfinance first, then falls back to Finnhub** if `FINNHUB_API_KEY` is configured. Raises if every source fails. `scan_ep_earnings` wraps the per-ticker call: a single failure notifies Telegram and skips that ticker; if every Phase C ticker fails, the scan raises `RuntimeError`. Mirrors the ep_news fix shipped 2026-05-13.

**Result summary breakdown:** when 0 candidates pass, the dashboard `result_summary` and Telegram alert include per-filter rejection counts (e.g. `rejects: atr=2 chg_open=1 cir=1`) so the operator can see *which* filter killed each batch without re-reading the raw rejection strings.

## Backtesting

Uses spreadsheet-based backtest with pre-computed gap-day features and forward return checkpoints.

**Data source:** Spikeet earnings data (2020–2026 corrected dataset).

**How to run:**

```bash
cd trading-bot
.venv/bin/python run_ep_backtest.py --type earnings
.venv/bin/python run_ep_backtest.py --type earnings --year 2025
.venv/bin/python run_ep_backtest.py --type earnings --trades      # show trade log
```

**Methodology:**
- Loads Excel file with gap-day features (OHLCV, ATR, CHG-OPEN%, Prev 10D, etc.)
- Computes derived features (atr_pct, close_in_range, downside_from_open)
- Applies Strategy B filter as vectorized pandas masks
- Simulates exits using forward return checkpoints (1D, 10D, 20D, 50D): if any checkpoint breaches -7% stop, exits at stop; otherwise exits at 50D return
- Known limitation: checkpoint stops miss intra-period dips (slightly optimistic)

**Results (2020–2026 corrected, 1,004 candidates):**

| Metric | Strategy B |
|--------|-----------|
| Trades | 340 |
| Win Rate | 44.7% |
| Avg Return | +4.95% |
| Profit Factor | 2.57 |
| Best Year | 2020 (66% WR, +14.2% avg) |
| Worst Year | 2021 (38% WR, +1.7% avg) |

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 800_000_000
stop_loss_pct: 7.0
max_hold_days: 50
b_min_close_in_range: 50.0
b_atr_pct_min: 2.0
b_atr_pct_max: 5.0
```

## Dashboard Parameter Display

The Strategies detail page (`/strategies/ep_earnings`) renders each parameter with its description, variation (A/B/C), and the phase/job where it's applied (scan vs execute). The descriptions and phase tags live in [`trading-bot/api/param_meta.py`](../../api/param_meta.py). Update that file whenever you add a new `ep_earnings_*` key to `config.yaml`.

The Variation column on the Trades tab is derived at read time from `Watchlist.meta["ep_strategy"]` — see [`api/variation.py`](../../api/variation.py). Legacy A/C positions still display their variant tag during the post-2026-05-08 transition window.

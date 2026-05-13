# EP News Strategy

EOD long swing on news-driven (non-earnings) gap-ups. Same timing as EP Earnings but excludes earnings events.

## Flow

1. **3:05 PM** — `scanner.py` finds news gappers (excludes earnings), `strategy.py` evaluates A/B filters. Passing entries → `Watchlist(stage="ready")` with execution payload in `metadata_json`.
2. **3:50–3:59 PM** — `job_execute` queries `Watchlist.stage="ready"` and places orders. Fires every minute (10 attempts) so a briefly-down bot or a transient Alpaca error still gets a trade in before close. **DB-driven + idempotent** — open-Position guard and <10-minute non-terminal Order guard block double-entry across retries. **Price refresh**: each attempt calls `core.execution.resolve_execution_price` to fetch live bid/ask and pick entry/stop — mid is used when it's at or modestly above the scan price (capped by `ep_execute_max_price_bump_pct`); wide quotes (`ep_execute_max_spread_pct`) skip and retry next minute. Stop is always recomputed from the actual entry.
3. **Ongoing exits** (in order of likely trigger):
   - **GTC -7% stop** at broker (both A and B), fires on intraday low
   - **9:40 AM ET D19+ partial** (added 2026-05-11): if at day 19+ and in profit, sell 40% market, move stop on remainder to entry × 1.05. Single-shot per position. See `monitor/position_tracker.py::check_ep_time_partial`.
   - **50-day max hold** at 3:55 PM EOD
   - Reconcile drift detector catches naked positions every 5 min (safety net)

**Note:** EP News scans at 3:05 PM (offset from EP Earnings at 3:00 PM) to avoid yfinance rate limiting from simultaneous per-ticker API calls.

### Watchlist stage semantics

| Stage | Meaning |
|-------|---------|
| `ready` | Staged for the next 15:50 execution |
| `triggered` | Order placed (set by `mark_triggered` after `_execute_entry` succeeds) |
| `expired` | EOD without trigger |

Strategy variant lives in `meta.ep_strategy` (A or B). Plugins pass `watchlist_setup_type="ep_news"` to `_execute_entry` so per-variant signal `setup_type` (e.g. `ep_news_a`, `ep_news_b`) still flips the right Watchlist row.

## Scanner Filters (`scanner.py`)

Same three-phase filter as EP Earnings, with differences:

| Difference | EP Earnings | EP News |
|------------|------------|---------|
| Market cap | >= $800M | >= $1B |
| Earnings | Required | Excluded |

**Earnings exclusion:** `_confirm_no_earnings()` returns `True` when the yfinance earnings calendar confirms no earnings today/yesterday. API failures are **not** swallowed — they propagate up, the scan fails, and a Telegram alert fires (per project error-handling policy). That way we never enter earnings-driven gaps as "news" due to a stale fallback.

## Strategy A (NEWS-Tight) — stop -7% | 57.6% WR, PF 5.34, +11.93% avg (2020–2026 corrected)

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | >= 50% |
| Downside from open | < 3% |
| ATR% | between 3% and 7% |
| Volume | < 3M shares |

## Strategy B (NEWS-Relaxed) — stop -7% | 49.1% WR, PF 4.24, +9.92% avg (2020–2026 corrected)

| Filter | Value |
|--------|-------|
| CHG-OPEN% | between 2% and 10% |
| Close in range | between 30% and 80% |
| Downside from open | < 6% |
| ATR% | between 3% and 7% |
| Volume | < 5M shares |

If both pass, Strategy A wins (tighter filters).

## Why both A and B (despite A having higher PF)

The 2026-05-08 head-to-head shows A and B catch **different market regimes**:
- A wins in 2020, 2021, 2023, 2025 (trending years) — its tight filters select highest-conviction breakouts.
- B-only wins in 2022, 2024, 2026 YTD (choppy / bear years) — its wider filters catch good setups that fail A's tightness.

Killing B would lose +11.7% (2022), +8.7% (2024), and +2.6% (2026 YTD) — three years where A was negative. So both stay.

## History

**2026-05-08: Strategy C dropped, Strategy B stop tightened from -10% to -7%.**

- **Strategy C** (1,714 trades over 6.4yr, 267/year) had PF 2.25 — barely better than buying every news gap (PF 1.95). The day-2 confirmation rule provided near-zero edge while burning 60% of total trade volume. Dropped despite 0/7 losing years because the volume cost vs marginal edge couldn't be justified.
- **Strategy B stop** moved from -10% to -7%. The wider stop was actually hurting: the corrected backtest showed PF 4.24 with -7% vs PF 3.48 with -10%. The -10% stop turned 2021 into a losing year (-0.7%); -7% makes it flat-positive.

Open positions tagged `ep_news_a`, `ep_news_b`, or `ep_news_c` continue to be managed by `monitor/position_tracker.py` until they exit naturally. Existing `ep_news_b` positions retain their original -10% stop (set at entry); only new entries get -7%.

**2026-04-21: Prev 10D change filter removed from A and B.** The Spikeet data column was sign-inverted vs yfinance. The 2026-05-08 corrected backtest confirmed it adds no edge on clean data either — keeping it cuts trade count 50% for marginal PF gain.

## Error handling

Feature computation (`compute_features`, `_compute_atr_pct`) **raises** when daily bars are missing or short (< 11 rows). The evaluator catches the error per ticker and records it in `rejections` with `is_data_error=True`. The Telegram summary labels these distinctly (`DATA ERROR`) so the operator can tell a real filter miss apart from a yfinance fetch failure.

If *every* candidate in a scan fails with a data error, `job_scan` raises `RuntimeError` so `_track_job` fires `JOB FAILED` — a batch-wide yfinance outage is a bug, not silent "0 passed filters".

This was added after the 2026-04-20 incident, where 5+ Strategy C candidates were silently dropped because `prev_10d_change_pct` and `atr_pct` silently fell back to `0.0` on short daily-bar returns.

## Backtesting

Uses spreadsheet-based backtest with pre-computed gap-day features and forward return checkpoints.

**Data source:** Spikeet news data (2020–2026 corrected dataset, 6,341 rows after junk filter).

**How to run:**

```bash
cd trading-bot
.venv/bin/python run_ep_backtest.py --type news              # both A and B
.venv/bin/python run_ep_backtest.py --type news --strategy A  # single strategy
.venv/bin/python run_ep_backtest.py --type news --year 2025   # single year
.venv/bin/python run_ep_backtest.py --type news --trades      # show trade log
```

**Methodology:**
- Same as EP earnings backtest
- Both A and B use -7% stop
- Known limitation: checkpoint stops miss intra-period dips (slightly optimistic)

**Results (2020–2026 corrected, 5,816 mcap-filtered candidates):**

| Metric | Strategy A | Strategy B-only (excl A overlap) |
|--------|-----------|-----------|
| Trades | 132 | 112 |
| Win Rate | 57.6% | 49.1% |
| Avg Return | +11.93% | +9.92% |
| Profit Factor | 5.34 | 4.24 |
| Best Year | 2020 (83% WR, +20.8% avg) | 2020 (68% WR, +18.0% avg) |
| Worst Year | 2022 (22% WR, -2.4%) | 2021 (27% WR, +0.7%) |

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 8.0
min_price: 3.0
min_market_cap: 1_000_000_000
exclude_earnings: true
max_hold_days: 50
a_stop_loss_pct: 7.0
b_stop_loss_pct: 7.0  # was 10.0 pre-2026-05-08
```

## Dashboard Parameter Display

The Strategies detail page (`/strategies/ep_news`) renders each parameter with its description, variation (A/B), and the phase/job where it's applied (scan vs execute). Descriptions and phase tags live in [`trading-bot/api/param_meta.py`](../../api/param_meta.py). Update that file whenever you add a new `ep_news_*` key to `config.yaml`.

The A/B badges on trade rows and pipeline job details are derived at read time via [`api/variation.py`](../../api/variation.py), which joins `Signal` back to the originating `Watchlist.meta["ep_strategy"]`. Legacy C positions still display their variant tag during the post-2026-05-08 transition window.

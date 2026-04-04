# Scanner Modules

Shared scanner infrastructure used by strategy plugins. Strategy-specific scanners live in `strategies/<name>/`.

## watchlist_manager.py — Watchlist Lifecycle

Manages the DB-backed watchlist with stage transitions:

```
watching → ready → active → triggered / expired / failed
```

- **Single-day strategies** (EP, parabolic): expire at EOD if not triggered
- **Multi-day strategies** (breakout): demote to "ready" at EOD for re-promotion next day

**Key functions:**
- `persist_candidates(candidates, setup_type, stage, scan_date, db_engine)` — insert/update watchlist entries
- `promote_ready_to_active(scan_date, db_engine)` — move breakout ready → active
- `expire_stale_active(today, db_engine, plugins)` — expire or demote based on plugin's `watchlist_persist_days`
- `run_nightly_scan(config, client, db_engine, progress_cb)` — orchestrate ranking + consolidation analysis
- `get_active_watchlist(db_engine)` — return all stage='active' entries

## consolidation.py — Consolidation Pattern Detection

Used by breakout nightly scan. Detects valid consolidation patterns (tighter range + near MA).

**Key functions:**
- `analyze_consolidation(ticker, config, daily_bars_df, consolidation_days)` — full analysis, returns qualifies + reasons
- `compute_atr(df, period=14)` — ATR series
- `detect_higher_lows(closes, window)` — slope > 0 via linear regression
- `detect_atr_contraction(atr_series, window, threshold)` — checks if range is tightening
- `check_near_ma(df, ma_period, tolerance_pct)` — price within tolerance of MA

## momentum_rank.py — Relative Strength Ranking

Ranks stocks by composite momentum score. Used in breakout nightly scan to select top candidates.

- `rank_by_momentum(tickers, config, client, top_n=20)` — returns top N by composite RS
- `compute_rs_score(df)` — composite = 50% 1m + 30% 3m + 20% 6m change

# EP Breakout Plugin Spec (`strategies/ep_breakout/`)

Status: **spec approved for build — not yet implemented.** Validated as EP 2.0
Track A ([research report](research/ep2_strategy_report.md), formal gates
[ep2_validation.md](research/ep2_validation.md): PASS 6/6, all 14 parameter
neighbors on the plateau).

## Validated configuration (do not tune without re-running scripts/validate_ep2.py)

```yaml
ep_breakout:
  enabled: true            # paper only until reviewed
  # -- gap event qualification (scan day) --
  gap_pct_min: 8.0         # vs prev close, same as other EP scanners
  prev_close_min: 3.0
  mktcap_b_min: 5.0        # BIG:   large caps only
  dollar_vol_min_m: 100.0  # LOUD:  gap-day close x volume >= $100M
  atr_pct_min: 3.0         # VOLATILE: 10d ATR / close >= 3%
  # NOTE: deliberately NO share-volume cap, NO chg_open cap, NO
  # close_in_range floor — those filters rejected 2026's winners.
  # -- breakout confirmation (daily 3:50 PM check) --
  bo_min_days: 4           # sessions of rest before entry is allowed
  bo_window: 15            # sessions to wait for confirmation, else expire
  bo_max_premium_pct: 5.0  # skip if confirm close > gap_high * 1.05
  # -- exits --
  stop_loss_pct: 8.0
  profit_target_pct: 30.0  # sell 33% at +30% (intraday limit or EOD check)
  profit_target_fraction: 0.33
  breakeven_trigger_pct: 15.0   # after a close >= +15%, stop -> entry
  trail_ma_days: 10             # close < 10d SMA of closes -> exit at close
  max_hold_days: 50
```

Expected performance (path-sim, 2021H2-2025 fit / 2026 OOS): PF 1.52 / 3.55,
avg +1.65% / +3.24% per trade, ~16 trades/yr. Honest expectation is the
plateau (PF ~1.4-1.6), NOT the research peak configs.

## Lifecycle (DB-driven, crash-safe — mirrors ep_earnings/ep_news patterns)

1. **Scan job (3:00 PM ET, with the other EP scans).** Detect today's
   qualifying gap events (Alpaca snapshots for gap/volume; yfinance for
   mktcap). Persist `Watchlist(stage="watching", setup_type="ep_breakout")`
   with meta: `gap_date`, `gap_high`, `gap_low`, `atr_pct`, `deadline`
   (= gap_date + bo_window trading sessions, via core/trading_calendar).
2. **Confirm job (3:50 PM ET daily).** For each watching ep_breakout row:
   - today's close (Alpaca latest 1m bar/snapshot):
   - `close < gap_low` -> stage="expired", notes="gap-low break"
   - past `deadline` -> stage="expired", notes="no confirmation"
   - `sessions_since_gap >= bo_min_days AND close > gap_high`:
       - `close > gap_high * (1 + bo_max_premium_pct/100)` -> expired
         ("chase guard")
       - else -> stage="ready" + execute immediately (same 3:50-3:59
         retry-loop pattern as ep_earnings_execute, idempotent on
         (ticker, setup_type)); entry = market order near close; place OTO
         GTC stop at entry x 0.92.
3. **Exits (monitor/position_tracker.py):**
   - GTC -8% stop at broker (existing machinery)
   - profit-target partial: NEW handler — sell 33% when price >= entry x
     1.30 (GTC limit order placed at entry alongside the stop, OCO-style if
     Alpaca supports; else EOD check like the time-partial pattern)
   - breakeven move: EOD check — after first close >= entry x 1.15, cancel
     GTC stop, replace at entry (reuse the cancel/replace + drift-detector
     hardening from the D19 partial work)
   - 10d MA-close trail: ENABLE `_check_ma_close_exits` for this setup
     (it is gated OFF for the other EP setups — keep it on here, that IS
     the runner exit)
   - 50d max-hold at 3:55 PM (existing)
   - The D19 time-partial does NOT apply to ep_breakout.
4. **Risk:** standard 0.3% risk/trade, 8% max position notional. Risk
   distance = 8% => position ~3.75% of equity typical.

## Trade-path rule compliance

All new code paths (confirm -> execute -> stop/target placement) follow the
CLAUDE.md trade-path rule: propagate exceptions to `_tracked_strategy_job`
or `notify()` before any return. Add the new sites to the audited list.

## Rollout

1. Implement plugin + tests (scanner unit tests w/ synthetic snapshots;
   confirm-job tests covering all four branches; exit tests extending
   tests/test_ep_time_partial.py patterns).
2. Deploy paper-only on Alpaca alongside existing strategies (no IB mirror
   until 20+ live fills).
3. `verify_day.py`: add checks — every watching row has a deadline; no row
   both expired and filled; entry fills within 1% of confirm close.
4. After 20 fills: run the simulator acceptance test (path-sim vs real
   fills, ±1.5pp), then decide scale-up / IB mirror.

## Parked: Track B (standalone momentum breakouts)

FAILED formal gates (first-half bleed 0.53 PF, mom63 knife-edge,
DELL-dependent 2026). Do not ship. Rework directions documented in
[ep2_strategy_report.md](research/ep2_strategy_report.md); the scanner +
1,250-event dataset remain in `scripts/scan_market_breakouts.py` +
`market data download/breakout_events.csv` for future iteration.

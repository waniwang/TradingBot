# EP Breakout (EP 2.0 Track A)

Long swing setup: **rested breakout above the gap-day high** of big, loud,
volatile gap events. Catalyst-agnostic (earnings AND news gaps qualify).

Validated 2026-07-05 on Massive.com daily-path data — formal gates PASS 6/6
([docs/research/ep2_validation.md](../../../docs/research/ep2_validation.md)),
spec at [docs/ep-breakout-plugin-spec.md](../../../docs/ep-breakout-plugin-spec.md).
Robust-config expectation (fit era 2021H2-2025): **PF ~1.5, +1.65%/trade,
~16 trades/yr**; 2026 OOS: PF 3.55 on n=4. Do NOT tune parameters without
re-running `scripts/validate_ep2.py` — every value sits on a checked plateau.

## Why this exists

The ep_earnings/ep_news filters (volume cap, chg_open cap, tight ATR band)
were tuned for quiet, orderly gaps and systematically rejected 2026's
high-volume AI-infra/memory theme leaders (MRVL, DELL, VRT...). Their
gap-day-close entry also bought faders. This strategy inverts both choices:
it *requires* loud + volatile, and it enters only after the market confirms.

## Scanner (3:15 PM ET, `scanner.py`)

| Filter | Value |
|---|---|
| Gap % (open vs prev close) | >= 8% |
| Prev close | >= $3 |
| Open > prev high | yes |
| Open > 200d SMA | yes |
| Dollar volume (gap day) | >= $100M (**floor**, not cap) |
| ATR% (10d) | >= 3% |
| Market cap | >= $5B |
| Security class | EQUITY |
| Earnings required | NO — catalyst-agnostic |

Passers become `Watchlist(stage="watching")` rows with gap-day reference
levels in meta. No same-day entry.

## Confirm state machine (3:50-3:59 PM ET daily, `plugin.py::job_confirm`)

For each watching row (up to `bo_window`=15 sessions):

1. Any close (or the live 3:50 price) **below the gap-day low** → expired
   ("gap-low break" — thesis broken).
2. Past 15 sessions with no trigger → expired ("no confirmation").
3. After **>= 4 sessions of rest**, first close **above the gap-day high**:
   - more than **5% past** the high → expired ("chase guard");
   - else **enter**: market order near close + OTO GTC stop at entry × 0.92.

Gap-day high/low are re-read from the completed daily bar every run (the
3:15 PM scan values are provisional). Rows promoted to `ready` just before
execution; a crash between promotion and fill is re-processed next run with
idempotency guards (open Position / recent Order per ticker+setup_type).

## Exits (`monitor/position_tracker.py`)

1. **GTC -8% stop** at broker (OTO with entry).
2. **+30% target partial** (9:40 AM `ep_breakout_partial_check`): sell 33%,
   re-place stop for the remainder at max(current stop, entry).
3. **Breakeven lock** (EOD): after a close >= entry × 1.15, stop → entry.
4. **10d MA-close trail** (EOD): close below the 10-day SMA exits the
   remainder — the validated runner exit, active from day 1 (the
   `_check_ma_close_exits` EP gate has an ep_breakout exception).
5. **50d max hold** (EOD).

NOT applied to this setup: the D19 time partial (ep_earnings/ep_news only)
and the MA stop-tightening in `_update_trailing_stop` (would front-run the
close-based trail with an untested intraday variant).

## Ops notes

- Jobs: `ep_breakout_scan` (15:15), `ep_breakout_confirm` (15:50-15:59),
  `ep_breakout_partial_check` (09:40). Dashboard metadata in
  `api/constants.py`, param descriptions in `api/param_meta.py`.
- Rollout gate: after ~20 live fills, run the path-simulator acceptance
  test (sim vs fills within ±1.5pp) before scale-up / IB mirroring.
- Tests: `tests/test_ep_breakout.py`.

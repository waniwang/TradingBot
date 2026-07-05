# EP 2.0 — Gap-Anchored + Standalone Momentum Breakouts (2026-07-05)

**Why:** current EP swing strategies are negative YTD in a year where 31% of
gap events ran +25%+. Root cause analysis
([ep_2026_optimization_report.md](ep_2026_optimization_report.md)): the old
filters were designed for quiet low-volume gaps and systematically reject
2026's loud AI-infra/memory theme leaders; gap-day-close entry catches faders.
Design rule for EP 2.0: fit ONLY on 2021H2-2025, 2026 strictly out-of-sample.

## Track A — gap-anchored rested breakout

Universe: >=8% gap events (Spikeet combined, `ep_events_combined.csv`),
**mcap >= $5B, gap-day dollar-vol >= $100M, ATR% >= 3** (big + loud +
volatile; no volume cap — the old <3M-share cap was the #1 winner-killer).

Entry (EOD-executable at 3:50 PM): after the gap, wait >= 4 sessions during
which price never CLOSES below the gap-day low; buy the first close above
the gap-day high within 15 sessions; skip if that close is already > 5%
above the gap high (chase guard). No confirmation = no trade — this is what
skips the gap-and-fade candidates that killed YTD.

Exits: stop -8%; sell 33% at +30%; stop -> breakeven after a +15% close;
exit on close < 10-day SMA of closes; 50d max hold.

**ROBUST config (final, gate-validated — see Honesty notes):**

| Era | n | WR | Avg | PF |
|---|---|---|---|---|
| 2021H2-2025 (fit) | 74 | 50% | +1.65% | 1.52 |
| 2026 YTD (OOS) | 4 | 75% | +3.24% | 3.55 |

(An earlier peak config — 5 sessions rest / 10-session window / 8% chase cap —
showed fit PF 2.52 but sat on a parameter cliff and was discarded; its
numbers appear in intermediate sweep runs, not here.)

2026 trades: VRT, DELL, AGX winners; PEN the one stop.

## Track B — standalone momentum-leader base breakout (no gap required)

Universe: full US market (Massive grouped-daily, 1,254 days, 2,728 liquid
symbols). Scanner (`scripts/scan_market_breakouts.py`): 20d avg dollar-vol
>= $50M, close >= $10, momentum leader (21d >= +25% or 63d >= +50%), >= 10-day
base with range <= 12%, close within 15% of 63d high, breakout close above
base high on >= 1.5x volume. 1,250 raw events 2021H2-2026.

Selection (fit-era tuned): **mom63 >= 80% AND within 5% of the 63d high**.
Entry at breakout close; stop under base low (clamped 3-12%); Track A exit
pack otherwise.

| Era | n | WR | Avg | PF |
|---|---|---|---|---|
| 2021H2-2025 (fit) | 91 | 54% | +2.60% | 1.64 |
| 2026 YTD (OOS) | 12 | 58% | +3.86% | 1.77 |

## Combined EP 2.0 (A + B, symbol-overlap deduped, A wins)

183 trades, ~40/yr. Trade list: `ep2_combined_trades.csv`.

| Year | n | WR | Avg | PF |
|---|---|---|---|---|
| 2021 (H2) | 9 | 22% | -3.07% | 0.54 |
| 2022 | 15 | 67% | -2.05% | 0.68 |
| 2023 | 40 | 48% | +2.99% | 2.04 |
| 2024 | 34 | 50% | +3.03% | 1.87 |
| 2025 | 69 | 57% | +5.74% | 3.01 |
| **2026 OOS** | **16** | **62%** | **+3.67%** | **1.90** |
| TOTAL | 183 | 53% | +3.38% | 1.95 |

2026 compounded (all-in sequential metric): **+48.0%**. Top OOS trades:
DELL +50% (B), INTC +31% (B), RVMD +15% (B), DELL +9.8% (A).

## Honesty notes

- 2026 OOS n=16 — encouraging sign, not statistical proof. The system's
  2026 sign is positive under a design frozen on 2021H2-2025.
- 2021H2/2022 are negative (bear tape; breakout systems bleed there).
  A QQQ>50dMA regime gate was tested and REJECTED — it barely moves total
  PF (1.95->2.05) and inverts 2023. At 0.3% account risk per trade the
  bear-year bleed is tolerable; revisit regime gating with better signals
  (breadth, net new highs) later, not as a blocker.
- Formal N-gates COMPLETE (ep2_validation.md): Track A **PASS 6/6** on the
  ROBUST config (bo_min_days 4, window 15, premium 1.05 — fit PF 1.52,
  worst neighbor 1.44, halves 1.54/1.50, 2026 OOS PF 3.55). The original
  peak (min_days 5/window 10/premium 1.08, PF 2.52) sat on a parameter
  cliff and was DISCARDED. Track B **FAIL** (first-half PF 0.53, mom63
  knife-edge, 2026 rests on DELL) — parked, not shipped.
- Live-fill acceptance test still blocked on the un-pushed GH Actions dump
  workflow (`_oneshot_dump_ep_trades.yml`, awaiting Shay's push approval).
- Data limitation: everything sits on the Massive Starter 5y window
  (2021-07-06+). 2020 regime untested.

## Next steps to go live (pending Shay)

1. ~~Formal gates~~ DONE — `scripts/validate_ep2.py`, Track A PASS 6/6.
2. ~~Plugin spec~~ DONE — `docs/ep-breakout-plugin-spec.md` (Track A only;
   Track B parked). Next: implement `strategies/ep_breakout/` + tests,
   paper-trade on Alpaca alongside existing strategies.
3. Decide fate of current EP earnings/news strategies (kept running per
   Shay 2026-07-05; Earnings B2-exits pack from the first study remains a
   gate-passing damage-control option).
4. Push approval for the trade-dump workflow -> re-verify simulator vs
   real fills. NOTE: none of today's work is committed yet.

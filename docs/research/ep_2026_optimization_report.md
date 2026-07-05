# EP Swing Optimization Report — 2026-07-05

**Question asked:** YTD performance is bad — are we taking profits too late and
turning winners into losers? Build variants of EP Earnings + EP News that beat
YTD.

**Method:** full daily price paths from Massive.com (Polygon-compatible,
Starter tier = rolling 5y window, so validation era = 2021-H2..2025; Shay
approved 2026-07-05) replacing the checkpoint-only Spikeet returns. Data gate:
99.5% price accuracy, 1.6% coverage loss ([massive_validation.md](massive_validation.md)).
2026 was NEVER used for fitting — entries/exits tuned on 2021H2-2025, 2026 is
a pure out-of-sample readout.

---

## Finding 0 (biggest): the old backtests were structurally optimistic

The checkpoint harness only tested the stop at 4 forward checkpoints; the real
broker GTC stop fires intraday. Same events, same era, same rules:

| Strategy | Checkpoint PF (retired) | True path-sim PF | Avg (chk → path) |
|---|---|---|---|
| Earnings B | 3.08 | **1.55** | +6.81% → +1.75% |
| News A | 4.73 | **2.97** | +11.43% → +5.03% |
| News B | 3.18 | **0.96** | +8.42% → **-0.17%** |

All documented PFs (2.57 / 5.34 / 4.24) descend from the checkpoint method and
should be retired. Much of "live underperforms backtest" was never regime — it
was measurement error.

Related: the **D19 time-partial slightly HURTS expectancy on real paths**
(Earnings B PF 1.63→1.55 stop-only vs production). Its checkpoint-measured
improvement (3.46→3.86) was an artifact. It still helps capital recycling.

## Finding 1: the winners-into-losers hypothesis — CONFIRMED, historically

Of historical (2021H2-2025) -7% stop-outs: **43-48% were ≥+5% green first**
(Earnings B 43%, News A 48%), median giveback ~10pp, median peak day D14-D19.
Full tables: [ep_2026_diagnosis.md](ep_2026_diagnosis.md). Exits are a real
lever — that part of the hypothesis is right.

## Finding 2: but 2026 has a second, bigger problem — entry quality

2026 candidates barely go green at all before dying (Earnings B median MFE
+2.7% vs +9.4% historical; News A 2026: 6 candidates, ALL stopped, median MFE
+1.6%, peak on day 1). Exit tuning can only harvest profit that exists.
2026's damage is roughly: measurement error (Finding 0) + weak candidate
follow-through + late exits, in that order.

---

## Recommendations

### 1. EP Earnings B2 — SHIP (exits-only variant passes all 10 gates)

```yaml
# exits (entries unchanged)
ep_earnings_stop_loss_pct: 6.0          # was 7.0
ep_earnings_profit_target_pct: 6.0      # NEW: sell 50% at +6%
ep_earnings_profit_target_fraction: 0.5
ep_time_partial_day: 15                 # was 19
ep_earnings_breakeven_trigger_pct: 12.0 # NEW: stop→entry after +12% close
ep_earnings_trail_mode: n_day_low       # NEW: close < 5-day low → exit
ep_earnings_trail_param: 5
ep_earnings_max_hold_days: 50           # unchanged
```

| Era | Baseline | B2-exits |
|---|---|---|
| 2021H2-2025 | PF 1.55, +1.75% avg, DD -70%, Sharpe 1.45 | **PF 1.89, +1.75% avg, DD -38%, Sharpe 2.07** |
| 2026 YTD (OOS) | -48.2% compounded, n=18 | **-37.9% compounded (+10.3pp)** |

Year-by-year: flips 2021 (-1.8%→+1.6%) and 2024 (-0.2%→+0.9%) from losing to
winning; no year degrades materially. Gates: 10/10 PASS
([ep_candidate_validation.md](ep_candidate_validation.md)).

### 2. EP Earnings B2-full — Shay's call (fails only the trade-count gate)

Adds `chg_open_min: 2.0` + `close_in_range_min: 60` on top of B2 exits.
Historical: PF 2.32, Sharpe 2.24, DD -29% — but n drops 252→171 (-32%,
fails G5 floor of -20%). 2026: **-24.6% compounded — the best 2026 result,
halving baseline damage.** Fewer, better trades vs statistical power.

### 3. EP News A2 — improve, but with honest caveats (G7 unattainable)

2026 News A = 6 candidates, all stopped, none ever green. No exit variant can
beat baseline by +5pp through six guaranteed losers — G7 is structurally
unpassable this year. On 2021H2-2025 evidence, A2-full is a large upgrade:

```yaml
# entries: close_in_range_min 50 → 70, atr_pct_min 3.0 → 2.5
# exits:
ep_news_a_stop_loss_pct: 7.0            # unchanged (8.0 tested better hist,
                                        #  worse in all-stop regimes — kept 7)
ep_news_a_profit_target_pct: 10.0       # NEW: sell 50% at +10%
ep_news_a_breakeven_trigger_pct: 12.0   # NEW
ep_news_a_trail_mode: n_day_low         # NEW: close < 10-day low → exit
ep_news_a_trail_param: 10
ep_news_a_max_hold_days: 30             # was 50
ep_news_a_time_partial: disabled        # D19 rule off for News A
```

Historical: PF 2.97→~5.3, avg +5.03%→+6.3%, DD -25%→-15%; 2026: -29.3% vs
-31.2% baseline (small OOS gain, median per-trade improved). 9/10 gates
(G7 fails, explained). Alternative: leave News A untouched and revisit after
the GH dump / more 2026 samples.

### 4. EP News B — DISABLE

Path-true PF 0.96 (loses money 2021H2-2025 after News A takes overlap), and
**zero of 1,152 exit combos** passed even loosened gates. Its documented PF
4.24 was checkpoint inflation + 2020 regime carry. Nothing to salvage via
exits; kill it or send entries back to research.

### 5. Process changes

- Retire checkpoint backtests for anything stop-related; path harness
  (`sweeps/path_harness.py`, 19 unit tests) is the reference simulator.
- Reconsider the D19 partial's role once B2's +6% target ships (they overlap).
- Pending: one-shot GH Actions dump of live fills (workflow written, awaiting
  push approval) → re-run diagnosis + per-fill acceptance test on real fills.

---

## Artifacts

| What | Where |
|---|---|
| Data validation gate | `docs/research/massive_validation.md` |
| Diagnosis (hypothesis test) | `docs/research/ep_2026_diagnosis.md` |
| G1-G10 scorecards + year tables | `docs/research/ep_candidate_validation.md` |
| Path simulator + tests | `sweeps/path_harness.py`, `sweeps/test_path_harness.py` |
| Sweep runs (5 stages) | `sweeps/runs/20260705_*` |
| Path cache (1,590 symbols) | `market data download/massive_daily/` |
| Fetcher / validators | `scripts/fetch_massive_daily_paths.py`, `scripts/validate_massive_vs_spikeet.py`, `scripts/ep_path_diagnosis.py`, `scripts/validate_candidate.py` |
| Live-fill dump workflow (unpushed) | `.github/workflows/_oneshot_dump_ep_trades.yml` |

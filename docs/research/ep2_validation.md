# EP 2.0 Formal Validation (N-gates)

Absolute gates for a new strategy (no production baseline). Fit era 2021H2-2025; 2026 strictly out-of-sample.

## Track A — gap-anchored rested breakout — **PASS**

Fit: n=74 WR=50% avg=+1.65% PF=1.52 (halves: 1.54 / 1.50)
OOS 2026: n=4 WR=75% avg=+3.24% PF=3.55

| Gate | Result |
|---|---|
| N1 fit PF>=1.5 | ✅ |
| N2 half balance | ✅ |
| N3 neighbor plateau | ✅ |
| N4 2026 avg>0 & PF>1 | ✅ |
| N5 2026 survives -best | ✅ |
| N6 exit mix sane | ✅ |

Neighbor plateau (fit-era PF, floor = 1.21):

| Neighbor | PF | OK |
|---|---|---|
| bo_min_days=3 | 1.81 | ✅ |
| bo_min_days=5 | 1.75 | ✅ |
| bo_window=10 | 1.52 | ✅ |
| bo_window=20 | 1.56 | ✅ |
| bo_max_premium=1.03 | 1.44 | ✅ |
| bo_max_premium=1.07 | 1.51 | ✅ |
| mktcap_b_min=2 | 1.26 | ✅ |
| ddv_min=50 | 1.41 | ✅ |
| atr_pct_min=2.5 | 1.29 | ✅ |
| stop_pct=10 | 1.37 | ✅ |
| pt=20 | 1.45 | ✅ |
| pt=40 | 1.46 | ✅ |
| be=None | 1.47 | ✅ |
| trail ma_close 15 | 1.41 | ✅ |

## Track B — standalone momentum breakout — **FAIL**

Fit: n=91 WR=54% avg=+2.60% PF=1.64 (halves: 0.53 / 2.95)
OOS 2026: n=12 WR=58% avg=+3.86% PF=1.77

| Gate | Result |
|---|---|
| N1 fit PF>=1.5 | ✅ |
| N2 half balance | ❌ |
| N3 neighbor plateau | ❌ |
| N4 2026 avg>0 & PF>1 | ✅ |
| N5 2026 survives -best | ❌ |
| N6 exit mix sane | ✅ |

Neighbor plateau (fit-era PF, floor = 1.31):

| Neighbor | PF | OK |
|---|---|---|
| mom63>=60 | 1.29 | ❌ |
| mom63>=100 | 0.97 | ❌ |
| dist<=3 | 1.75 | ✅ |
| dist<=8 | 1.42 | ✅ |
| pt=20 | 1.69 | ✅ |
| pt=40 | 1.66 | ✅ |
| be=None | 1.57 | ✅ |
| trail ma_close 15 | 1.77 | ✅ |

# Overall: **FAIL**

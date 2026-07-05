# EP Candidate Validation — G1-G10 Scorecards

Path-simulated on Massive daily bars. Baseline = production entries + production exits (-7% stop, D19 partial, 50d hold). Era: 2021-H2..2025 validation, 2026 out-of-sample.

## EP Earnings B2-exits (exits only) — **PASS**

G10 note: Diagnosis: 43% of hist stop-outs were >=+5% green first, 10pp median giveback -> early 50% target at +6% + tighter stop + short trail harvests exactly that.

| Gate | Result |
|---|---|
| G1 PF>=0.9x | ✅ |
| G2 avg>=0.9x & >=1pp | ✅ |
| G3 no year flips | ✅ |
| G4 DD<=1.2x | ✅ |
| G5 n>=0.8x | ✅ |
| G6 PF>=1.5 both halves | ✅ |
| G7 2026 +5pp compounded | ✅ |
| G8 median>0 & no one-outlier | ✅ |
| G9 neighbors pass G1-G4 | ✅ |
| G10 mechanism matches diagnosis | ✅ |

### Year-by-year (2021H2-2025)

| Year | Base n | Base WR | Base avg | Base PF | Cand n | Cand WR | Cand avg | Cand PF |
|---|---|---|---|---|---|---|---|---|
| 2021 | 23 | 34.78% | -1.82% | 0.60 | 23 | 60.87% | 1.61% | 1.88 |
| 2022 | 22 | 59.09% | 3.76% | 2.30 | 22 | 68.18% | 3.95% | 3.74 |
| 2023 | 48 | 58.33% | 1.45% | 1.49 | 48 | 60.42% | 1.38% | 1.66 |
| 2024 | 69 | 50.72% | -0.16% | 0.96 | 69 | 57.97% | 0.91% | 1.43 |
| 2025 | 90 | 62.22% | 3.79% | 2.39 | 90 | 60.00% | 2.08% | 2.07 |

### 2026 YTD (out-of-sample)

- Baseline: n=18, avg -3.47%, compounded -48.2%
- Candidate: n=18, avg -2.50%, compounded -37.9%

## EP Earnings B2-full (entries + exits) — **FAIL**

G10 note: Same exit mechanism; entry tighten (chg_open>2, CIR>=60) targets 2026's weak-follow-through gappers (med MFE +2.7%).

| Gate | Result |
|---|---|
| G1 PF>=0.9x | ✅ |
| G2 avg>=0.9x & >=1pp | ✅ |
| G3 no year flips | ✅ |
| G4 DD<=1.2x | ✅ |
| G5 n>=0.8x | ❌ |
| G6 PF>=1.5 both halves | ✅ |
| G7 2026 +5pp compounded | ✅ |
| G8 median>0 & no one-outlier | ✅ |
| G9 neighbors pass G1-G4 | ✅ |
| G10 mechanism matches diagnosis | ✅ |

### Year-by-year (2021H2-2025)

| Year | Base n | Base WR | Base avg | Base PF | Cand n | Cand WR | Cand avg | Cand PF |
|---|---|---|---|---|---|---|---|---|
| 2021 | 23 | 34.78% | -1.82% | 0.60 | 13 | 61.54% | 2.84% | 2.67 |
| 2022 | 22 | 59.09% | 3.76% | 2.30 | 14 | 78.57% | 5.55% | 5.75 |
| 2023 | 48 | 58.33% | 1.45% | 1.49 | 34 | 61.76% | 1.69% | 1.85 |
| 2024 | 69 | 50.72% | -0.16% | 0.96 | 49 | 59.18% | 1.36% | 1.69 |
| 2025 | 90 | 62.22% | 3.79% | 2.39 | 61 | 63.93% | 2.75% | 2.56 |

### 2026 YTD (out-of-sample)

- Baseline: n=18, avg -3.47%, compounded -48.2%
- Candidate: n=12, avg -2.22%, compounded -24.6%

## EP News A2-exits (exits only) — **FAIL**

G10 note: Diagnosis: News A stops were 48% >=+5% green first, 10.9pp giveback; hold-30 + 10-day-low trail + 50% target at +10% captures the D19 median peak earlier.

| Gate | Result |
|---|---|
| G1 PF>=0.9x | ✅ |
| G2 avg>=0.9x & >=1pp | ✅ |
| G3 no year flips | ✅ |
| G4 DD<=1.2x | ✅ |
| G5 n>=0.8x | ✅ |
| G6 PF>=1.5 both halves | ✅ |
| G7 2026 +5pp compounded | ❌ |
| G8 median>0 & no one-outlier | ❌ |
| G9 neighbors pass G1-G4 | ✅ |
| G10 mechanism matches diagnosis | ✅ |

### Year-by-year (2021H2-2025)

| Year | Base n | Base WR | Base avg | Base PF | Cand n | Cand WR | Cand avg | Cand PF |
|---|---|---|---|---|---|---|---|---|
| 2021 | 3 | 66.67% | 4.17% | 2.79 | 3 | 66.67% | 6.33% | 3.71 |
| 2022 | 5 | 40.00% | -3.32% | 0.21 | 5 | 60.00% | 0.75% | 1.27 |
| 2023 | 10 | 80.00% | 7.03% | 6.02 | 10 | 80.00% | 8.81% | 8.50 |
| 2024 | 15 | 60.00% | 1.18% | 1.42 | 15 | 66.67% | 1.89% | 1.95 |
| 2025 | 8 | 62.50% | 15.32% | 6.84 | 8 | 62.50% | 11.29% | 5.30 |

### 2026 YTD (out-of-sample)

- Baseline: n=6, avg -6.00%, compounded -31.2%
- Candidate: n=6, avg -6.74%, compounded -34.2%

## EP News A2-full (entries + exits) — **FAIL**

G10 note: Same exit mechanism; entry tighten (CIR>=70) selects stronger closes, the dominant hist factor.

| Gate | Result |
|---|---|
| G1 PF>=0.9x | ✅ |
| G2 avg>=0.9x & >=1pp | ✅ |
| G3 no year flips | ✅ |
| G4 DD<=1.2x | ✅ |
| G5 n>=0.8x | ✅ |
| G6 PF>=1.5 both halves | ✅ |
| G7 2026 +5pp compounded | ❌ |
| G8 median>0 & no one-outlier | ✅ |
| G9 neighbors pass G1-G4 | ✅ |
| G10 mechanism matches diagnosis | ✅ |

### Year-by-year (2021H2-2025)

| Year | Base n | Base WR | Base avg | Base PF | Cand n | Cand WR | Cand avg | Cand PF |
|---|---|---|---|---|---|---|---|---|
| 2021 | 3 | 66.67% | 4.17% | 2.79 | 4 | 75.00% | 7.17% | 5.10 |
| 2022 | 5 | 40.00% | -3.32% | 0.21 | 6 | 66.67% | 2.67% | 2.15 |
| 2023 | 10 | 80.00% | 7.03% | 6.02 | 10 | 80.00% | 7.19% | 7.12 |
| 2024 | 15 | 60.00% | 1.18% | 1.42 | 13 | 69.23% | 2.84% | 2.67 |
| 2025 | 8 | 62.50% | 15.32% | 6.84 | 4 | 100.00% | 20.01% | nan |

### 2026 YTD (out-of-sample)

- Baseline: n=6, avg -6.00%, compounded -31.2%
- Candidate: n=5, avg -6.69%, compounded -29.3%


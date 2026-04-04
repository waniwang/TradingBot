# Signals Module

Shared signal infrastructure and indicator functions. Strategy-specific signal logic lives in `strategies/<name>/`.

## base.py — Indicators & SignalResult

### SignalResult Dataclass

Output of all signal checks:
- `ticker, setup_type, side, entry_price, stop_price`
- `orh, orb_low, gap_pct, volume_ratio, fired_at, notes`
- Property: `risk_per_share` = |entry - stop|

### Opening Range

- `compute_orh(candles, n_minutes=5)` — max high of first N minutes
- `compute_orb_low(candles, n_minutes=5)` — min low of first N minutes

### Indicators

- `compute_vwap(candles)` — cumulative VWAP as pd.Series
- `compute_sma(closes, period)` — simple moving average (last value)
- `compute_atr_from_list(highs, lows, closes, period=14)` — ATR from lists
- `compute_avg_volume(volumes, period=20)` — average daily volume

### RVOL (Relative Volume)

- `compute_rvol(today_volume, avg_daily_volume, minutes_since_open)` — time-of-day normalized
- Uses empirical U-shaped intraday volume profile (anchors at 5m, 15m, 30m, etc.)
- RVOL = 2.0 at 9:35 AM means 2x the expected volume for that time of day

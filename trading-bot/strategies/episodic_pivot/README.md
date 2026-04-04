# Episodic Pivot (EP) Strategy

Single-day long setup on unexpected catalysts (earnings/news gap-ups). Scans pre-market, trades intraday.

## Flow

1. **6:00 AM** — `scanner.py` finds pre-market gappers via Alpaca screener
2. **9:30 AM** — `signal.py` monitors 1m bars for ORH breakout entry
3. **EOD** — candidates expire (single-day persistence)

## Scanner Filters (`scanner.py`)

| Filter | Threshold |
|--------|-----------|
| Gap % | >= 10% (configurable) |
| Price | > $5 |
| Premarket volume | >= 100k shares |
| Prior 6-month gain | < 50% (reject stocks that already ran) |
| Symbol | <= 5 chars, alpha only |

Source: Alpaca market movers screener (top 50), then filtered.

## Entry Signal (`signal.py`)

All conditions must pass:

1. Price > ORH (5-min opening range high)
2. Extension <= 5% above ORH (anti-chase guard)
3. RVOL >= 2.0x 20-day avg (time-of-day normalized)
4. Gap >= min threshold

**Stop**: LOD, capped at 1.5x ATR(14).

## Key Config (`config.yaml`)

```yaml
min_gap_pct: 10.0
volume_multiplier: 2.0
max_extension_pct: 5.0
stop_atr_mult: 1.5
prior_rally_max_pct: 50.0
```

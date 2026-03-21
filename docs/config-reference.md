# Config Reference

Full schema for `config.yaml`. All parameters are documented below.

---

## Full config.yaml Template

```yaml
environment: paper             # paper | live

alpaca:
  api_key: YOUR_ALPACA_API_KEY
  secret_key: YOUR_ALPACA_SECRET_KEY

risk:
  risk_per_trade_pct: 1.0      # % of portfolio risked per trade
  max_positions: 4             # max concurrent open positions
  max_position_pct: 15.0       # max single position as % of portfolio notional
  daily_loss_limit_pct: 3.0    # halt day if daily loss exceeds this %
  weekly_loss_limit_pct: 5.0   # halt week if weekly loss exceeds this %

strategies:
  enabled:                                   # which setups to scan & trade
    - episodic_pivot
    - breakout
    # - parabolic_short                      # disabled — negative expectancy in backtests

signals:
  # --- Shared ---
  orh_minutes: 5                           # opening range duration in minutes
  atr_period: 14                           # ATR lookback period for all ATR calculations

  # --- Episodic Pivot (EP) ---
  ep_min_gap_pct: 10.0                     # min overnight gap % to qualify
  ep_volume_multiplier: 2.0               # min RVOL vs 20d avg (time-of-day normalized)
  ep_max_extension_pct: 5.0              # max % above ORH before skipping (anti-chase guard)

  # --- Breakout ---
  breakout_consolidation_days_min: 10      # min trading days in consolidation range
  breakout_consolidation_days_max: 40      # max trading days in consolidation range
  consolidation_atr_ratio: 0.95            # ATR contraction threshold (lower = stricter)
  consolidation_ma_tolerance_pct: 3.0      # max % distance from 10d/20d MA
  consolidation_prior_move_pct: 30.0       # min % advance before consolidation
  breakout_volume_multiplier: 1.5          # min RVOL vs 20d avg (time-of-day normalized)
  breakout_max_extension_pct: 3.0         # max % above ORH before skipping (anti-chase guard)

  # --- Parabolic Short (disabled) ---
  parabolic_min_gain_pct: 50.0             # legacy fallback — used if per-cap keys not set
  parabolic_min_gain_pct_largecap: 50.0    # large-cap (price > $50): min gain %
  parabolic_min_gain_pct_smallcap: 200.0   # small-cap (price < $20): min gain %
  parabolic_min_days: 3                    # min consecutive up days

exits:
  partial_exit_after_days: 3              # sell partial after this many days in trade
  partial_exit_gain_threshold_pct: 15.0   # only partial exit if gain >= this %
  partial_exit_fraction: 0.40             # fraction of position to sell (40%)
  trailing_ma_period: 10                  # MA period for trailing stop (10 or 20)

universe:
  min_price: 5.0                          # min stock price for scanner universe
  min_avg_volume: 100000                  # min 20-day average volume

telegram:
  bot_token: YOUR_TELEGRAM_BOT_TOKEN
  chat_id: YOUR_TELEGRAM_CHAT_ID

database:
  url: sqlite:///trading_bot.db           # SQLite for paper; change to postgres for live
```

---

## Parameter Details

### `environment`
| Value | Description |
|---|---|
| `paper` | Uses `TradingClient(paper=True)` — Alpaca paper trading account |
| `live` | Uses `TradingClient(paper=False)` — live Alpaca account with real money |

**Always start with `paper`. Only change to `live` after completing the pre-live checklist.**

---

### `alpaca`
| Key | Description |
|---|---|
| `api_key` | Alpaca API key — get from https://alpaca.markets -> paper account |
| `secret_key` | Alpaca secret key — shown once on creation, save it |

Paper and live accounts have **separate** API keys in Alpaca. Make sure you're using the right pair.
Can also be set via env vars `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.

---

### `risk`
| Key | Default | Description |
|---|---|---|
| `risk_per_trade_pct` | `1.0` | % of portfolio value risked per trade |
| `max_positions` | `4` | Max concurrent open positions |
| `max_position_pct` | `15.0` | Max single position notional as % of portfolio |
| `daily_loss_limit_pct` | `3.0` | Daily loss % that halts trading for the day |
| `weekly_loss_limit_pct` | `5.0` | Weekly loss % that halts trading for the week |

For live trading start, use `risk_per_trade_pct: 0.5` and `max_positions: 2`.

---

### `strategies`
| Key | Default | Description |
|---|---|---|
| `enabled` | `[episodic_pivot, breakout]` | List of setup types to scan and trade. Valid values: `episodic_pivot`, `breakout`, `parabolic_short` |

Parabolic short is commented out by default due to negative backtest expectancy.

---

### `signals`

#### Shared
| Key | Default | Description |
|---|---|---|
| `orh_minutes` | `5` | Duration (minutes) for opening range high calculation |
| `atr_period` | `14` | ATR lookback period, used for stop-loss caps |

#### Episodic Pivot (EP) entry
| Key | Default | Description |
|---|---|---|
| `ep_min_gap_pct` | `10.0` | Minimum premarket gap % to qualify as EP candidate |
| `ep_volume_multiplier` | `2.0` | RVOL threshold (time-of-day normalized vs 20d avg) |
| `ep_max_extension_pct` | `5.0` | Max % above ORH before skipping (anti-chase guard) |

#### Breakout entry
| Key | Default | Description |
|---|---|---|
| `breakout_volume_multiplier` | `1.5` | RVOL threshold (time-of-day normalized vs 20d avg) |
| `breakout_max_extension_pct` | `3.0` | Max % above ORH before skipping (anti-chase guard) |

#### Consolidation (breakout setup — nightly scan)
| Key | Default | Description |
|---|---|---|
| `breakout_consolidation_days_min` | `10` | Minimum days in consolidation phase |
| `breakout_consolidation_days_max` | `40` | Maximum days in consolidation phase |
| `consolidation_atr_ratio` | `0.95` | ATR must contract below this ratio vs prior ATR |
| `consolidation_ma_tolerance_pct` | `3.0` | % tolerance for "near MA" check (both 10d and 20d) |
| `consolidation_prior_move_pct` | `30.0` | Min % move in ~2 months before consolidation |

#### Parabolic short (disabled)
| Key | Default | Description |
|---|---|---|
| `parabolic_min_gain_pct` | `50.0` | Legacy flat threshold (used if per-cap keys absent) |
| `parabolic_min_gain_pct_largecap` | `50.0` | Threshold for stocks with price > $50 |
| `parabolic_min_gain_pct_smallcap` | `200.0` | Threshold for stocks with price < $20 |
| `parabolic_min_days` | `3` | Minimum consecutive up days for parabolic qualification |

For stocks between $20 and $50, the threshold is linearly interpolated between the small-cap and large-cap values.

---

### `exits`
| Key | Default | Description |
|---|---|---|
| `partial_exit_after_days` | `3` | Minimum days in trade before partial exit triggers |
| `partial_exit_gain_threshold_pct` | `15.0` | Minimum unrealized gain % for partial exit to trigger |
| `partial_exit_fraction` | `0.40` | Fraction of shares to sell on partial exit |
| `trailing_ma_period` | `10` | MA period for trailing stop (10 = 10-day MA) |

Both `partial_exit_after_days` AND `partial_exit_gain_threshold_pct` must be satisfied simultaneously for partial exit to trigger.

**Trailing exit**: after partial exit is done, the position is closed at EOD if the daily close is below the trailing MA. This is a close-based check, not an intraday touch.

---

### `universe`
| Key | Default | Description |
|---|---|---|
| `min_price` | `5.0` | Minimum stock price for scanner universe (filters penny stocks) |
| `min_avg_volume` | `100000` | Minimum 20-day average daily volume |

---

### `telegram`
| Key | Description |
|---|---|
| `bot_token` | Bot token from @BotFather on Telegram |
| `chat_id` | Your Telegram chat ID (use @userinfobot to find it) |

Telegram alerts are sent for: bot started, premarket scan start/finish, nightly scan start/finish, watchlist ready, entry order placed, entry fill, stop fill (via reconciliation), trading halted, unprotected position, EOD summary, errors.

---

### `database`
| Key | Default | Description |
|---|---|---|
| `url` | `sqlite:///trading_bot.db` | SQLAlchemy connection string |

For PostgreSQL: `postgresql://user:password@host:5432/trading_bot`

---

## Environment Variables

Sensitive keys can be set as environment variables (recommended for production). Env vars override `config.yaml` values.

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DATABASE_URL=...
```

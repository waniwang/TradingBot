# Config Reference

Full schema for `config.yaml`. All parameters are documented below.

---

## Full config.yaml Template

```yaml
environment: paper             # paper | live

alpaca:
  api_key: YOUR_ALPACA_API_KEY
  secret_key: YOUR_ALPACA_SECRET_KEY

polygon:
  api_key: YOUR_POLYGON_KEY

risk:
  risk_per_trade_pct: 1.0      # % of portfolio risked per trade
  max_positions: 4             # max concurrent open positions
  max_position_pct: 10.0       # max single position as % of portfolio notional
  daily_loss_limit_pct: 3.0    # halt day if daily loss exceeds this %
  weekly_loss_limit_pct: 5.0   # halt week if weekly loss exceeds this %

signals:
  ep_min_gap_pct: 10.0                     # min gap % to qualify as Episodic Pivot
  breakout_consolidation_days_min: 10      # min days for valid consolidation
  breakout_consolidation_days_max: 40      # max days for valid consolidation
  parabolic_min_gain_pct: 50.0             # min gain % for parabolic short candidate
  parabolic_min_days: 3                    # min consecutive up days for parabolic

exits:
  partial_exit_after_days: 3              # sell partial after this many days in trade
  partial_exit_gain_threshold_pct: 15.0   # only partial exit if gain >= this %
  partial_exit_fraction: 0.40             # fraction of position to sell (40%)
  trailing_ma_period: 10                  # MA period for trailing stop (10 or 20)

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
| `api_key` | Alpaca API key — get from https://alpaca.markets → paper account |
| `secret_key` | Alpaca secret key — shown once on creation, save it |

Paper and live accounts have **separate** API keys in Alpaca. Make sure you're using the right pair.
Can also be set via env vars `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.

---

### `polygon`
| Key | Description |
|---|---|
| `api_key` | Polygon.io API key — get from https://polygon.io/dashboard |

Polygon.io free tier has delayed data. Use a paid plan for real-time pre-market scanning.

---

### `risk`
| Key | Default | Description |
|---|---|---|
| `risk_per_trade_pct` | `1.0` | % of portfolio value risked per trade |
| `max_positions` | `4` | Max concurrent open positions |
| `max_position_pct` | `10.0` | Max single position notional as % of portfolio |
| `daily_loss_limit_pct` | `3.0` | Daily loss % that halts trading for the day |
| `weekly_loss_limit_pct` | `5.0` | Weekly loss % that halts trading for the week |

For live trading start, use `risk_per_trade_pct: 0.5` and `max_positions: 2`.

---

### `signals`
| Key | Default | Description |
|---|---|---|
| `ep_min_gap_pct` | `10.0` | Minimum premarket gap % to qualify as EP candidate |
| `breakout_consolidation_days_min` | `10` | Minimum days in consolidation phase |
| `breakout_consolidation_days_max` | `40` | Maximum days in consolidation phase |
| `parabolic_min_gain_pct` | `50.0` | Minimum % gain over `parabolic_min_days` to qualify |
| `parabolic_min_days` | `3` | Minimum consecutive up days for parabolic |

---

### `exits`
| Key | Default | Description |
|---|---|---|
| `partial_exit_after_days` | `3` | Minimum days in trade before partial exit triggers |
| `partial_exit_gain_threshold_pct` | `15.0` | Minimum unrealized gain % for partial exit to trigger |
| `partial_exit_fraction` | `0.40` | Fraction of shares to sell on partial exit |
| `trailing_ma_period` | `10` | MA period for trailing stop (10 = 10-day MA) |

Both `partial_exit_after_days` AND `partial_exit_gain_threshold_pct` must be satisfied simultaneously for partial exit to trigger.

---

### `telegram`
| Key | Description |
|---|---|
| `bot_token` | Bot token from @BotFather on Telegram |
| `chat_id` | Your Telegram chat ID (use @userinfobot to find it) |

Telegram alerts are sent for: entry fill, stop hit, partial exit, daily loss limit hit, EOD summary.

---

### `database`
| Key | Default | Description |
|---|---|---|
| `url` | `sqlite:///trading_bot.db` | SQLAlchemy connection string |

For PostgreSQL: `postgresql://user:password@host:5432/trading_bot`

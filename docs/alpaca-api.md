# Alpaca API Reference

Focused cheat sheet of what this bot uses from Alpaca, what we tested and rejected, and measured performance on **our free paper tier**. Authoritative API docs live at https://docs.alpaca.markets; this file captures only what took **discovery work** to learn — information Alpaca's docs don't give you.

Our SDK: `alpaca-py >= 0.43.0`. All usage is wrapped in [`executor/alpaca_client.py`](../trading-bot/executor/alpaca_client.py) — grep there for examples.

## Auth & tier

- **Env vars** (set in `trading-bot/.env`): `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`.
- **Mode**: `config.alpaca.paper` (default `true`) → `paper-api.alpaca.markets`. Set to `false` for live.
- **Subscription**: free paper. Confirmed accessible:
  - Trading API (full)
  - Market Data — IEX feed (free)
  - Screener (`/v1beta1/screener/stocks/*`) — works despite being new
  - Clock / Calendar / Assets (free)
- **NOT tested / probably paid**: SIP feed, OPRA options data, news API.

## Endpoints we use

Trading client (`alpaca.trading.client.TradingClient`):

| Method | Purpose | alpaca_client.py |
|---|---|---|
| `get_clock()` | Market open/next_open/next_close | `get_market_clock()` :136, `is_market_open()` :158 |
| `get_calendar(start, end)` | Holiday-aware trading-day check | `is_trading_day()` :168 |
| `get_account()` | Portfolio value, cash | `get_portfolio_value()` :194, `get_cash()` :202 |
| `get_all_positions()` | Open positions | `get_open_positions()` :207 |
| `submit_order()` | Limit / stop / market orders | `place_limit_order()` :230, `place_stop_order()` :265 |
| `cancel_order_by_id()` | Cancel pending order | `cancel_order()` :303 |
| `get_order_by_id()` | Fill status polling | `get_order_status()` :338 |
| `get_all_assets(status=ACTIVE, asset_class=US_EQUITY)` | Tradable universe | `get_tradable_universe()` :622 |

Historical data client (`StockHistoricalDataClient`):

| Method | Purpose | alpaca_client.py | Notes |
|---|---|---|---|
| `get_stock_snapshot()` | Prev close, today's OHLCV, latest trade/quote | `get_snapshots()` :584 | **The fast path for gap scanning** — see Performance below |
| `get_stock_bars(TimeFrame.Day)` | Daily OHLCV history | `get_daily_bars()` :578, `get_daily_bars_batch()` :758 | Batch path is Alpaca-first with yfinance fallback for short/empty symbols |
| `get_stock_bars(TimeFrame.Minute)` | 1-min intraday bars | `get_candles_1m()` :491 | |
| `get_stock_latest_bar()` | Latest bar for one ticker | `get_latest_bar()` :474 | |
| `get_stock_latest_quote()` | NBBO quote | `get_realtime_quote()` :456 | |

Streaming client (`StockDataStream`):

| Method | Purpose | alpaca_client.py |
|---|---|---|
| `subscribe_bars()` + websocket | Live 1-minute bars for intraday signals | `subscribe_quotes()` :360, `_start_stream()` :388 |

## Endpoints we tested and REJECTED (do not re-discover)

### `get_market_movers()` (top gainers / losers)

- **Hard capped** at `top ≤ 50`. Requesting `top=100` returns `HTTP 400: invalid top: should not be larger than 50`.
- **Dominated by penny stocks, warrants, SPAC units**. On 2026-04-17, the **minimum `percent_change` in the top 50 was +24.55%**. Any stock gapping 8–24% (the entire sweet spot for our EP strategies) is invisible.
- **Returns `percent_change`** (current vs prev close), **not gap%** (open vs prev close). Different signal.
- Real EP-quality candidates on 2026-04-17 (UAL +8.3%, ALV +12.4%, JBLU +8.3%, LAYS +12.2%, UMC +11.6%, BANF +8.3%, CPA +8.8%, LAKE +25.6%) were either missing from top-50 or crowded out by <$3 warrants.
- **Conclusion**: unusable for our strategy. Code wrapper `get_market_movers_gainers()` at alpaca_client.py:564 remains callable but is not wired into any scanner.

### `get_most_actives()` (by volume)

- Also capped at `top ≤ 50`. Returns large-caps and popular names (NVDA, TSLA, NFLX) but volume ranking doesn't help with gap detection — active ≠ gapping.
- Not integrated.

## Measured performance (paper tier, IEX feed)

All measurements from a single test on 2026-04-18 (Saturday; data = Friday's close):

| Call | Symbols | Wall time | Coverage |
|---|---:|---:|---|
| `get_stock_snapshot` | 500 | 0.9 s | 498 / 500 had valid `previous_daily_bar` |
| `get_stock_snapshot` | 1,500 | 0.8 s | 1,495 / 1,500 |
| `get_stock_snapshot` | 3,000 | 0.9 s | 2,992 / 3,000 (99.7%) |
| `get_stock_snapshot` | 5,060 (full universe) | ~2 s | ~99.7% |
| Full gap scan (snapshots → filter gap≥8%, price≥$3, open>prev_high) | 5,060 | ~5 s | 80 gappers found |

**Takeaway:** the snapshot endpoint is the workhorse for anything universe-wide. It's 300–500× faster than our previous yfinance bulk-download approach (which was 30–40 min on the same universe with ~2000 wasted retries on delisted tickers).

## Known quirks

- **IEX vs SIP feed**: paper tier uses IEX. The "IEX covers only ~2% of stocks" note in the codebase refers to **realtime intraday trade/quote coverage** for `get_stock_latest_trade()` — NOT to daily snapshots or bars. For `get_stock_snapshot` and `get_stock_bars(Day)`, IEX returns **~99.7%** of tickers correctly because `daily_bar` / `previous_daily_bar` aggregate all exchanges.
- **Weekends / holidays**: snapshot endpoints return the **last trading day's** data with `last_updated` set accordingly. Safe to call any time.
- **`daily_bar.open`**: locked at 9:30 AM ET once the market opens. By 3 PM ET (when our EP scans run), `daily_bar.open` is the true 9:30 open price. Before 9:30 it's `None`.
- **Delisted / invalid symbols**: snapshot API silently omits them from the response dict. No exceptions, no retries needed — completely unlike yfinance which raises on delisted names.
- **Symbol validity for `get_all_assets`**: results include preferred shares, warrants (`.WS`, `W` suffix), sub-$1 names. Our wrapper filters to `len(symbol) <= 5 and symbol.isalpha()` — exchange in `{NYSE, NASDAQ}`. See `get_tradable_universe()` :622.
- **Order side**: `OrderSide.BUY` / `OrderSide.SELL` from `alpaca.trading.enums`. For shorts, submit `SELL` with `TimeInForce.DAY` and no prior position — Alpaca flags it as short if shortable.
- **BarSet lookup**: when iterating `get_stock_bars` response, use `bars.data[symbol]` dict — NOT `bars.get(symbol)`. `BarSet` has no `.get()` method.
- **Screener client init**: `ScreenerClient(api_key=, secret_key=)` — no `paper=` parameter. Market data endpoints are shared between paper and live.

## Endpoints we haven't used but are available (future work)

| Endpoint | When you'd reach for it |
|---|---|
| Corporate Actions API (`/v2/corporate-actions/...`) | Reliable dividend / split data without yfinance. Could replace some `yf.Ticker().info` lookups. |
| News API (`/v1beta1/news`) | News-gap strategy catalyst detection. Replaces the "this gap had news" heuristic in EP News strategy. |
| Options data (`/v1beta1/options/...`) | Volatility-context overlays on equity trades; future options strategies. |
| `get_stock_latest_trade()` | Sub-second live price — useful for tight intraday entry timing. Currently we poll quotes. |
| `get_stock_auctions()` | Opening / closing auction prints — could refine OR-based signals. |
| Watchlist API (Alpaca-side) | Sync bot watchlist with Alpaca's UI. Niceity, not required. |
| Portfolio History (`/v2/account/portfolio/history`) | Source of truth for equity curve — currently we rebuild this from `daily_pnl`. |

## When in doubt

1. Prefer snapshot or bars over scrape-based APIs (yfinance) whenever possible — Alpaca is faster, cleaner, and doesn't rate-limit at our volume.
2. Don't try to replace yfinance `.info` (market cap, quoteType, earnings calendar) with Alpaca — Alpaca doesn't have fundamentals data. Keep the yfinance path for those.
3. Screener endpoints are a trap for this strategy profile — they bias toward pennies. Use `get_snapshots()` on a full universe instead.
4. Test performance on a Saturday before assuming endpoint latency — our tradable universe has meaningful weekly churn; a "free" API call today may be rate-limited tomorrow. Current measurements above reflect paper tier on 2026-04-18.

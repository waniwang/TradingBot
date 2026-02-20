# Risks & Mitigations

## Technical Risks

| Risk | Mitigation |
|---|---|
| **Alpaca free tier limited data (IEX ~2% coverage)** | Use yfinance for batch daily bars; Alpaca for real-time quotes on watchlist only |
| **Slippage on ORH entries** (momentum names move fast) | Use limit orders with tolerance (entry <= ORH + 0.5%); skip if price runs away |
| **yfinance batch download is slow** (~14 min for 1500 tickers) | Run in pre-market (6 AM); batch in groups of 500; cache with parquet files |
| **API rate limits (Alpaca)** | Cache scan results; use bulk snapshot endpoints instead of per-symbol calls |
| **Database corruption / data loss** | SQLite WAL mode; daily backup; use PostgreSQL for production |
| **Clock drift / timing issues** | All scheduler jobs in ET timezone (`America/New_York`); use NTP-synced server |
| **Bot runs during market holiday** | APScheduler with NYSE trading calendar check before job execution |

---

## Strategy Risks

| Risk | Mitigation |
|---|---|
| **Parabolic short requires locate access** | Alpaca supports shorting on margin accounts; verify margin enabled before live |
| **Overfitting in backtest** | Walk-forward validation; hold out 2024 data as out-of-sample test |
| **Gap risk on open positions overnight** | Defined risk per trade (1%); max 4 positions; diversification limits damage |
| **Low liquidity names — order never fills** | Minimum ADV (average daily volume) filter in scanners: >= 500k shares/day |
| **False signals during broad market selloffs** | Optional: check SPY/QQQ trend filter — only trade longs if market in uptrend |
| **Earnings risk on breakout positions** | Check earnings date; avoid holding through earnings unless intentional |
| **Wide stops on volatile names** | ATR cap on stops: 1x ATR for breakout, 1.5x ATR for EP |

---

## Operational Risks

| Risk | Mitigation |
|---|---|
| **VPS goes down during market hours** | Process supervisor (systemd/supervisord); alert on restart; flat positions on startup |
| **Config accidentally changed to `live` early** | Confirm prompt before executing orders when `environment: live`; separate live config file |
| **API keys exposed in code** | Store in environment variables or `.env` file; never commit to git; use `.gitignore` |
| **Telegram bot sends to wrong chat** | Verify `chat_id` matches personal account; test with `/start` before live use |

---

## Known Limitations

1. **EP catalyst detection**: The bot detects gap-ups automatically but cannot read news/earnings reports to classify the *type* of catalyst. Manual review is still needed to confirm the catalyst is genuinely surprising.

2. **Short selling**: Parabolic shorts require an Alpaca margin account. Verify margin is enabled before running parabolic short strategy live.

3. **Level 2 / order flow**: The current design does not use Level 2 data for entries. Adding this later could improve timing.

4. **Overnight gap protection**: Alpaca stop orders are GTC but may not trigger at the exact stop price on gap opens. A gap below the stop price results in a market-open fill, not the stop price.

5. **Pre-market fill quality**: Pre-market trading has wider spreads. The bot does not currently trade pre-market; all entries are after 9:35 AM ET.

6. **Backtest limitations**: Backtests use daily bars only (no intraday data), so entries are approximated. Results are indicative but not exact. Actual live performance may differ.

7. **yfinance reliability**: yfinance scrapes Yahoo Finance and may occasionally fail or return incomplete data. Some tickers may be delisted or renamed (e.g., SQ -> XYZ). Parquet caching mitigates repeat failures.

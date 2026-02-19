# Risks & Mitigations

## Technical Risks

| Risk | Mitigation |
|---|---|
| **Moomoo per-symbol subscription cost for large universe** | Use Polygon.io for scanning; only subscribe Moomoo for final watchlist (~10-20 stocks) |
| **Slippage on ORH entries** (momentum names move fast) | Use limit orders with tolerance (entry ≤ ORH + 0.5%); skip if price runs away |
| **OpenD disconnects** | Reconnect handler in `moomoo_client.py`; OpenD watchdog process; alert on disconnect |
| **API rate limits (Polygon.io)** | Cache scan results; use bulk snapshot endpoints instead of per-symbol calls |
| **Database corruption / data loss** | SQLite WAL mode; daily backup; use PostgreSQL for production |
| **Clock drift / timing issues** | All times in UTC internally; convert to ET only for display; use NTP-synced server |
| **Bot runs during market holiday** | APScheduler with NYSE trading calendar check before job execution |

---

## Strategy Risks

| Risk | Mitigation |
|---|---|
| **Parabolic short requires locate access** | Start with long setups only; add shorts after confirming Moomoo locate access |
| **Overfitting in backtest** | Walk-forward validation; hold out 2024 data as out-of-sample test |
| **Gap risk on open positions overnight** | Defined risk per trade (1%); max 4 positions; diversification limits damage |
| **Low liquidity names — order never fills** | Minimum ADV (average daily volume) filter in scanners: ≥ 500k shares/day |
| **False signals during broad market selloffs** | Optional: check SPY/QQQ trend filter — only trade longs if market in uptrend |
| **Earnings risk on breakout positions** | Check earnings date; avoid holding through earnings unless intentional |

---

## Operational Risks

| Risk | Mitigation |
|---|---|
| **VPS goes down during market hours** | Process supervisor (systemd/supervisord); alert on restart; flat positions on startup |
| **Config accidentally changed to `real` early** | Confirm prompt before executing orders when `environment: real`; separate live config file |
| **API keys exposed in code** | Store in environment variables or `.env` file; never commit to git; use `.gitignore` |
| **Telegram bot sends to wrong chat** | Verify `chat_id` matches personal account; test with `/start` before live use |

---

## Known Limitations

1. **EP catalyst detection**: The bot detects gap-ups automatically but cannot read news/earnings reports to classify the *type* of catalyst. Manual review is still needed to confirm the catalyst is genuinely surprising.

2. **Short selling**: Parabolic shorts require a Moomoo margin account with locate capability. Not available in all regions. Implement longs first.

3. **Level 2 / order flow**: The current design does not use Level 2 data for entries. Adding this later could improve timing.

4. **Overnight gap protection**: Moomoo stop orders are typically GTC (Good Till Cancelled) but may not trigger on gap opens. Be aware that a gap below the stop price results in a market-open fill, not the stop price.

5. **Pre-market fill quality**: Moomoo pre-market trading has wider spreads. The bot does not currently trade pre-market; all entries are after 9:35 AM ET.

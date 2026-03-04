# DemoBot Engine – Weak Points & Fixes

## Why "Profit 0" for Open Positions

**For open positions, `realized_pnl = 0` is expected.** Realized PnL is only recorded when you partially or fully close (TP1, TP2, or stop hit). Until then, PnL is unrealized. The dashboard now shows **unrealized PnL** for open positions (labeled with "(u)").

---

## Fixes Applied

1. **Position monitor skipped when signal was missing**
   - If the engine restarted or no recent signal existed for a symbol, the position monitor skipped that symbol and never checked stop/TP.
   - **Fix:** When there is no signal in memory, the engine fetches the latest signal from the DB and still runs stop/TP checks.

2. **Position monitor skipped when signal was `None`**
   - Positions were only managed when a signal was available. If no signal could be found, `_manage_position` was never called.
   - **Fix:** `_manage_position` is always called. When `signal` is `None`, stop/TP hit detection still works; only stop tightening after TP1 is skipped.

3. **Dashboard showed 0 PnL for open positions**
   - Only realized PnL was shown. Open positions always showed 0.00.
   - **Fix:** Positions API returns `spot_by_symbol`. The dashboard computes and shows unrealized PnL for open positions (labeled with "(u)").

---

## Weak Points (Limitations)

| Weak Point | Description | Mitigation |
|------------|-------------|------------|
| **Paper trading only** | No real execution; fills are simulated | Expected for demo; use live broker integration for real trades |
| **1-minute candle lag** | Stop/TP uses 1m OHLC; fast moves can be missed | Position monitor runs every 1s; uses spot + last candle high/low |
| **Single candle for stop** | Uses only the last candle’s high/low for stop detection | May miss intra-candle wicks; consider adding current tick/spot checks |
| **Engine restart gap** | After restart, `latest_signal` is empty until the next tick | Fixed: signal is fetched from DB when missing |
| **Market data dependency** | No market data → no position management for that symbol | Ensure Binance/Kraken (or dashboard) is providing data |
| **Symbol key consistency** | `state.latest_market_data` keys must match position symbols (e.g. "ETH" vs "ETH-USD") | SYMBOLS config must use the same format for symbols |
| **No limit orders** | Paper broker uses market-style fills at entry | Real brokers would use limit orders at entry/stop/TP |
| **Stop tightening needs signal** | After TP1, stop is tightened using signal percentiles | If no signal, tightening is skipped; basic stop/TP still works |

---

## Recommended Checks

1. **`/status`** – Confirm `data_fresh: true` and `can_produce_signals: true` for symbols.
2. **Engine logs** – Look for "no market data" or "market data stale".
3. **Position monitor** – With the fixes, positions are checked every 1s as long as market data is available.
4. **Dashboard** – Open positions show unrealized PnL with a "(u)" label; closed positions show realized PnL.

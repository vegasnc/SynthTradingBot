# DemoBot Engine – Weak Points & Fixes

## Trade 2 (3/4/2026 11:16 AM): Entry < Stop, TP1, TP2 – FIXED

**Your BTC long:** Entry 73079, Stop 73168, TP1 73418, TP2 73542 — Entry was below stop and all targets.

**Root cause:** When TP1 was hit, we updated the stop using signal percentiles. The new stop could end up above entry for a long, creating invalid levels.

**Fix applied:** We no longer update the stop when price hits TP1 or TP2. The bot only **closes** (partial close at TP1/TP2, full close at stop). Stop stays as originally set at entry.

---

## Trade 3 (3/4/2026 9:17 AM): Long Bias but Short Structure – ACCEPTED

**Your BTC long:** Entry 71822, Stop 71249, TP1 71440, TP2 71505 — Bias was long but levels looked short-style (TPs below entry).

**Design choice:** This is treated as a label display nuance. The bot does **not** reject such trades. Levels are used as-is; close at stop, TP1, or TP2 when price hits them.

---

## Trade Analysis: Entry > TP1/TP2 (Fixed)

**Your BTC long:** Entry 71822, TP1 71440, TP2 71505, Stop 71249 — Entry was above TP1/TP2.

**What happened:** For a long, TP1 and TP2 should be above entry (price rises = profit). Here they were below entry, so "take profit" at TP1/TP2 would lock in a loss. The position made +40.77 because:
1. TP1 and TP2 (80% of size) were hit at a loss
2. The remaining 20% runner was held until price rose much higher (~76.8k)
3. The runner profit offset the TP1/TP2 losses

**Root cause:** `entry = max(p35, spot - 0.20 * central_range)` could exceed tp1 when spot was high. No validation enforced entry < tp1 for long.

**Fix applied:** Entry is now clamped so for long: `entry ≤ tp1 - 0.2%`, and for short: `entry ≥ tp1 + 0.2%`. Invalid levels add `invalid_levels_long/short` and block the trade.

---

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
| **No stop tightening** | Bot never updates stop after entry | On TP1/TP2 hit: partial close only. Stop unchanged. |
| **Profit too low / loss too high** | TP1/TP2 close 40% each; stop closes 100%. Risk/reward and placement affect PnL | Consider wider stops, higher TP targets, or different partial-close % |
| **Entry vs spot timing** | Entry is a limit level; paper broker fills at entry ± slippage | Real brokers use limit orders; slippage can worsen entry |
| **80% closed at TP1/TP2** | Only 20% runner remains after TP1+TP2 | Big moves favor the runner; normal moves lock most profit early |

---

## Recommended Checks

1. **`/status`** – Confirm `data_fresh: true` and `can_produce_signals: true` for symbols.
2. **Engine logs** – Look for "no market data" or "market data stale".
3. **Position monitor** – With the fixes, positions are checked every 1s as long as market data is available.
4. **Dashboard** – Open positions show unrealized PnL with a "(u)" label; closed positions show realized PnL.

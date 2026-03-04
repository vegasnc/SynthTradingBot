# Synth API Calling Options

Configure how often the bot calls the Synth API. (Daily credit limit: 20,000)

## When the bot calls Synth API

1. **Scheduled refresh** – Uses **adaptive intervals** based on forecast uncertainty:
   - **Uncertainty < 0.02**: 20 min (very confident forecast)
   - **Uncertainty < 0.05**: 15 min
   - **Uncertainty ≥ 0.08 (crypto)**: 5 min (volatile – refresh more often)
   - **Default**: 10 min

2. **Early refresh (price divergence)** – If market price moves **≥ X%** within the lookback period, the bot calls Synth immediately for a fresh forecast instead of waiting for the scheduled refresh.

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTH_REFRESH_MINUTES` | 10 | Fallback baseline (adaptive logic overrides). |
| `SYNTH_PRICE_CHANGE_REFRESH_PCT` | 0.3 | Early refresh trigger: if price moves ≥ X% within the period, call API immediately. Lower = more frequent early refreshes. |
| `SYNTH_PRICE_CHANGE_PERIOD_MINUTES` | 2 | Volatility lookback: compare current price to price from N minutes ago. |

## API endpoints used

1. **Prediction percentiles** (`/insights/prediction-percentiles`) – Per asset, for signals and position management. Uses adaptive + early refresh.
2. **Liquidation insight** (`/insights/liquidation`) – When opening a crypto position (once per open).

## Recommended: calls per hour per asset

| Scenario | Est. calls/hour/asset | Notes |
|----------|------------------------|-------|
| **Conservative** | 4–6 | High uncertainty (0.08+), 5‑min refresh; ~5–12/hour |
| **Typical** | 6–10 | 10‑min adaptive + early refresh on 0.3% move |
| **Aggressive** | 10–20 | 5‑min refresh when uncertain + 0.3% early trigger |

With **4 assets** and typical usage: ~4 × 8 × 24 ≈ **768 prediction calls/day**. Liquidation adds ~1 call per position open. Well under 20,000/day.

## Why more frequent refreshes can help

- **Stale forecasts** – A 10‑min old prediction can be off when price has moved; newer predictions give better entry/stop/TP levels.
- **Position management** – Each tick uses the latest signal (and thus the latest prediction) for `tighten_stop`; fresher predictions improve trailing-stop logic.
- **Price divergence** – When price moves 0.3%+ in 2 min, the bot refreshes early so entry/exit levels stay aligned with the market.
- **High uncertainty** – For volatile assets, the bot already shortens the refresh interval to 5 min.

## If positions are failing (stop-outs)

Possible causes:

- **Stops too tight** – Stop is derived from percentiles; high volatility can hit stops quickly.
- **Stale prediction** – Entry/stop/TP from an old forecast may no longer fit current price action.
- **Entry confirmation** – `confirm_entry` requires momentum; if it fails often, fewer trades open.

**Mitigation:** Use 0.3% for `SYNTH_PRICE_CHANGE_REFRESH_PCT` and rely on adaptive refresh (already enabled). If Synth supports it, consider slightly wider stops or relaxed entry filters.

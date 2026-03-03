# Synth API Calling Options

Configure how often the bot calls the Synth API. (Daily credit limit: 20,000)

## Environment variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SYNTH_REFRESH_MINUTES` | 10 | Scheduled refresh interval. Prediction API is called every N minutes per asset. |
| `SYNTH_PRICE_CHANGE_REFRESH_PCT` | 0.5 | Early refresh trigger. If price moves ≥ X% within the period, call API immediately. |
| `SYNTH_PRICE_CHANGE_PERIOD_MINUTES` | 2 | Volatility lookback. Compare current price to price from N minutes ago. |

## API endpoints used

1. **Prediction percentiles** (`/insights/prediction-percentiles`) – Called per asset for strategy signals. Uses the refresh schedule above.
2. **Liquidation insight** (`/insights/liquidation`) – Called when opening a crypto position (once per open).

## Examples

**Fewer API calls (save credits):**
```
SYNTH_REFRESH_MINUTES=15
SYNTH_PRICE_CHANGE_REFRESH_PCT=2.0
```

**More responsive to market moves:**
```
SYNTH_REFRESH_MINUTES=5
SYNTH_PRICE_CHANGE_REFRESH_PCT=0.5
```

## Credit usage estimate

With 4 assets, 10-min refresh: ~4 × 6 × 24 ≈ **576 prediction calls/day**. Liquidation adds ~1 call per position open. Well under 20,000/day.

# DemoBot Trading Strategy Introduction

## Overview

DemoBot is a quantitative trading bot that uses **Synth API's probabilistic price predictions** to generate trading signals for crypto assets (BTC, ETH, XAU) and equities (AAPLX, TSLAX, SPYX, NVDAX, GOOGLX). The core philosophy is:

> **Synth predictions set reference levels; realized market prices trigger execution.**

---

## 1. Core Concepts

### 1.1 Synth Predictions

The bot fetches 1-hour horizon probabilistic forecasts from [Synth API](https://api.synthdata.co), which provides:

| Percentile | Meaning |
|------------|---------|
| **P05** | 5% chance price goes below this (tail risk for longs) |
| **P20** | 20% chance price goes below this (value zone for longs) |
| **P35** | 35% chance price goes below this (entry zone for longs) |
| **P50** | Median forecast price (fair value) |
| **P65** | 35% chance price goes above this (entry zone for shorts) |
| **P80** | 20% chance price goes above this (overbought zone) |
| **P95** | 5% chance price goes above this (tail risk for shorts) |

### 1.2 Key Metrics Derived from Percentiles

```
Range        = P95 - P05     (total forecast price distribution)
Central Range = P80 - P20     (main trading band)
Uncertainty  = Range / Spot   (relative volatility forecast)
```

### 1.3 Edge Calculation

Edge measures how far the current spot price is from the median forecast:

```
Long Edge  = (P50 - Spot) / Spot   (positive when spot is below fair value)
Short Edge = (Spot - P50) / Spot   (positive when spot is above fair value)
```

**Edge Threshold**: A signal is only valid if `|Edge| >= 0.15 × Uncertainty`. This ensures the expected move is meaningful relative to forecast volatility and reduces marginal trades.

---

## 2. Signal Generation

### 2.1 Bias Determination

The bot determines trade direction based on:

1. **P50 vs Spot**: If P50 > Spot → bullish bias (long); if P50 < Spot → bearish bias (short)
2. **Market Momentum Override**: The bot calculates realized momentum over the past 2 hours using 5-minute candles. If price rose > 0.1%, market is "bullish"; if fell > 0.1%, "bearish"

### 2.2 Market Strength Logic

The bot prefers trades aligned with market direction:

| Market Direction | Preferred Trade | Counter-Trend Allowed If... |
|------------------|-----------------|----------------------------|
| Bullish | Long | Short edge ≥ 2.0× Long edge |
| Bearish | Short | Long edge ≥ 2.0× Short edge |

This **counter-trend multiplier (2.0×**, configurable via `MARKET_STRENGTH_COUNTER_TREND_MULTIPLIER`) ensures the bot only trades against the trend when the edge is clearly better, reducing losing counter-trend trades.

### 2.3 Signal Filters

A signal must pass all these filters to be "allowed":

| Filter | Criteria | Rationale |
|--------|----------|-----------|
| **Uncertainty** | ≤ 8% (crypto) / 5% (equity) | Avoid trades in extremely volatile conditions |
| **Edge Threshold** | Edge ≥ 15% of Uncertainty | Ensure meaningful expected profit; fewer but higher-quality signals |
| **Min Stop Distance** | Stop ≥ 0.6% of spot from entry | Avoid being stopped out by normal noise |
| **Min Risk:Reward** | Reward/risk ≥ 1.2 (TP1 vs stop distance) | Only take trades with adequate upside vs risk |
| **Entry Confirmation** | Momentum alignment in 1m + 5m candles | Confirm price is moving in signal direction |
| **Valid Levels** | Entry < TP1 (long) or Entry > TP1 (short), Stop valid | Ensure trade geometry is mathematically correct |

---

## 3. Entry, Stop-Loss, and Take-Profit Levels

### 3.1 Long Trade Levels

```
Entry = max(P35, Spot - 0.20 × CentralRange)
        capped at: TP1 - 0.2% of Spot

Stop  = ((P05 + P20) / 2) - 0.08 × CentralRange

TP1   = (P50 + P65) / 2
TP2   = P65
TP    = (TP1 + TP2) / 2   (unified target)
```

### 3.2 Short Trade Levels

```
Entry = min(P65, Spot + 0.20 × CentralRange)
        floored at: TP1 + 0.2% of Spot

Stop  = ((P80 + P95) / 2) + 0.08 × CentralRange

TP1   = (P35 + P50) / 2
TP2   = P35
TP    = (TP1 + TP2) / 2   (unified target)
```

### 3.3 Visual Representation

```
Long Trade:
  Stop ───────── Entry ───────── TP1 ───── TP2
  (P05/P20)      (P35)           (P50+P65) (P65)
       ↑                              ↑
    Risk Zone                    Profit Zone

Short Trade:
  TP2 ───── TP1 ───────── Entry ───────── Stop
  (P35)    (P35+P50)      (P65)           (P80/P95)
       ↑                              ↑
  Profit Zone                    Risk Zone
```

---

## 4. Entry Confirmation

Before opening a position, the bot confirms momentum using recent candles:

```python
# For LONG:
1. Price near or below entry level (within 0.3% tolerance)
2. 5-minute candle is bullish (close > open or close > VWAP)
3. Momentum: two consecutive up-closes OR break above prior 1m high

# For SHORT:
1. Price near or above entry level (within 0.3% tolerance)
2. 5-minute candle is bearish (close < open or close < VWAP)
3. Momentum: two consecutive down-closes OR break below prior 1m low
```

If entry confirmation fails, the signal is logged as `entry_confirmation_failed` and no trade opens.

---

## 5. Position Sizing

Position size is calculated using fixed-fractional risk:

```
Risk Amount = Account Equity × Risk %
Per-Unit Risk = |Entry - Stop|
Raw Qty = Risk Amount / Per-Unit Risk
Max Qty = (Account Equity × Max Symbol Exposure) / Entry Price
Final Qty = min(Raw Qty, Max Qty)
```

**Default Parameters:**

| Parameter | Crypto | Equity |
|-----------|--------|--------|
| Risk % per trade | 1-2% | 1-2% |
| Max Symbol Exposure | 10% | 10% |
| Max Portfolio Exposure | 50% | 50% |

---

## 6. Position Management

### 6.1 TP1 / TP2 Partial Close (50% / 50%)

To reduce risk and lock in profit, the bot closes in two steps:

1. **TP1 hit**: Close **50%** of the position. Remaining qty is halved; realized PnL is credited for the closed half. A stop-loss that triggers later applies only to the remaining 50%.
2. **TP2 hit**: Close the **remaining 50%** (full exit).

So: *remaining funds = initial funds − (entry × qty in open positions)*. After TP1, only half the position is still at risk from a stop.

### 6.2 Real-Time Exit Monitoring

The bot monitors positions every 1 second and exits as follows:

1. **TP2 hit**: Full close (remaining qty).
2. **TP1 hit** (first time only): Partial close 50%; position stays open with reduced qty and `tp1_closed` set.
3. **Stop-Loss hit**: Close full remaining qty (so if TP1 already closed 50%, only the other 50% is lost).
   - Uses candle low/high to catch intra-candle stop hits

### 6.3 PnL Calculation

```
Long PnL  = (Exit Price - Entry Price) × Quantity
Short PnL = (Entry Price - Exit Price) × Quantity
```

---

## 7. Additional Trade Filters

### 7.1 Pre-Open Checks

| Filter | Threshold | Purpose |
|--------|-----------|---------|
| **Minimum Expected Profit** | Expected profit > 0.5% | Avoid low-reward trades |
| **Volatility Width** | (P95-P05)/Spot ≥ MIN_VOLATILITY_WIDTH | Ensure enough range for trade |
| **Fee-Aware Profit** | Expected profit > 3× trading fee | Account for round-trip fees |

### 7.2 Multi-Position Rules

- **Opposite-side positions**: Allowed (e.g., long + short on same asset)
- **Same-side positions**: Only if new entry is better than existing
  - Long: New entry must be lower than existing entry
  - Short: New entry must be higher than existing entry
- **No chasing**: If spot is already beyond existing entry in profit direction, skip

---

## 8. Synth API Refresh Logic

### 8.1 Adaptive Refresh Intervals

| Uncertainty Level | Refresh Interval |
|-------------------|------------------|
| < 2% | Every 20 minutes |
| < 5% | Every 15 minutes |
| < 8% | Every 10 minutes |
| ≥ 8% (crypto) | Every 5 minutes |

### 8.2 Early Refresh Trigger

If price moves ≥ **0.3%** within the past **2 minutes**, the bot calls Synth API immediately for fresh predictions.

### 8.3 Daily Credit Budget

- **Limit**: 20,000 API calls/day
- **Typical usage**: ~4 assets × 8 calls/hour × 24h = ~768 calls/day (well under limit)

---

## 9. Liquidation-Aware Stop Adjustment (Crypto)

For leveraged crypto positions, the bot fetches liquidation insight from Synth:

1. Find liquidation cluster prices with probability ≥ 20%
2. If a cluster is within 1% of the stop price, adjust stop to be **0.3% outside** the cluster
3. Reduce position size by 15% when liquidation-adjusted

This prevents stop-loss hunting at high-liquidation zones.

---

## 10. Equity-Specific: MOO/MOC Trading

For stocks (AAPLX, TSLAX, etc.), the bot uses Market-on-Open (MOO) and Market-on-Close (MOC) orders:

| Time (ET) | Action |
|-----------|--------|
| Pre-market (8:00-9:25) | Evaluate equity signals, queue MOO orders |
| 9:25-9:30 | Submit MOO orders |
| 15:40-15:50 | Submit MOC orders to close all equity positions |

---

## 11. Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                         SYNTH API                                │
│  /insights/prediction-percentiles → P05, P20, P35, P50, P65...  │
│  /insights/liquidation → Liquidation probability levels          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      STRATEGY ENGINE                             │
│  1. Parse percentiles → Compute Range, CentralRange, Uncertainty │
│  2. Derive Edge = (P50 - Spot) / Spot                            │
│  3. Apply market strength bias (momentum over 2h)                │
│  4. Build Decision: entry, stop, TP1, TP2, TP                    │
│  5. Filter: uncertainty, edge threshold, entry confirmation      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      POSITION MANAGER                            │
│  1. Size position: Risk % / Per-Unit-Risk                        │
│  2. Check exposure limits (symbol, portfolio)                    │
│  3. Open position via broker                                     │
│  4. Monitor every 1s: check stop/TP against spot                 │
│  5. Close position when hit, record PnL                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 12. Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LOOP_SECONDS` | 60 | Main scheduler tick interval |
| `SYNTH_REFRESH_MINUTES` | 10 | Baseline Synth API refresh |
| `SYNTH_PRICE_CHANGE_REFRESH_PCT` | 0.3 | Early refresh trigger % |
| `SYNTH_PRICE_CHANGE_PERIOD_MINUTES` | 2 | Lookback for price change |
| `MARKET_STRENGTH_LOOKBACK_MINUTES` | 120 | Momentum calculation period |
| `COUNTER_TREND_MULTIPLIER` | 1.5 | Edge multiplier for counter-trend |
| `MIN_EXPECTED_PROFIT` | 0.005 | Minimum expected profit (0.5%) |
| `MIN_VOLATILITY_WIDTH` | 0.005 | Minimum P95-P05 range |
| `TRADING_FEE_RATE` | 0.001 | Trading fee (0.1%) |
| `MAX_SYMBOL_EXPOSURE` | 0.10 | Max 10% of account per symbol |
| `MAX_PORTFOLIO_EXPOSURE` | 0.50 | Max 50% of account in open trades |

---

## 13. Why This Strategy Works

1. **Forward-Looking**: Uses Synth's probabilistic forecasts instead of backward-looking indicators (ATR, RSI)
2. **Risk-Defined**: Every trade has a pre-defined stop and target based on the forecast distribution
3. **Market-Adaptive**: Refresh intervals adapt to volatility; more frequent calls in uncertain conditions
4. **Trend-Aware**: Prefers trades aligned with realized momentum, with a higher bar for counter-trend
5. **Fee-Conscious**: Only takes trades where expected profit exceeds transaction costs
6. **Liquidation-Aware**: Adjusts stops to avoid high-liquidation zones (crypto)

---

## 14. Glossary

| Term | Definition |
|------|------------|
| **Spot** | Current market price |
| **Edge** | Expected % move toward median forecast |
| **Uncertainty** | Forecast range relative to spot price |
| **Central Range** | P80 - P20 (main trading band) |
| **TP (Take-Profit)** | Target exit price for profit |
| **Stop** | Exit price to limit loss |
| **MOO** | Market-on-Open order (equity) |
| **MOC** | Market-on-Close order (equity) |
| **Entry Confirmation** | Momentum check before opening position |
| **Counter-Trend** | Trading against the market direction |

---

*Document generated for DemoBot trading system. Strategy parameters are configurable via environment variables.*

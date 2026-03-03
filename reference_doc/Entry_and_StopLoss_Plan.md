# Plan: Announcement Entry Point & Stop-Loss Point from Synth Prediction APIs

How to derive **entry signals** (when to announce/enter a trade) and **stop-loss levels** (when to exit to limit losses) using Synth's probabilistic forecast APIs.

---

## Core Principle: Synth as Reference, Realized Price for Execution

| Aspect | Role |
|--------|------|
| **Synth predictions** | **Reference** – used to compute and set the price levels for entry and stop-loss. Synth tells you *where* to place your levels. |
| **Realized (market) price** | **Execution trigger** – you place orders in the live market and execute when the **actual realized price** hits the levels derived from Synth. |

**Flow:**
1. Fetch Synth predictions → compute **reference levels** (entry price, stop-loss price)
2. Monitor **realized price** (live market data)
3. Execute when **realized price** reaches the Synth-derived levels

All comparisons and triggers in the logic below use **realized price** against levels that were **determined from Synth** as the reference.

---

## Overview

| Goal | Synth-Driven Approach |
|------|------------------------|
| **Entry Point** | Use Synth percentiles, LP probabilities, and prediction paths to define reference levels; enter when **realized price** hits those levels |
| **Stop-Loss Point** | Use Synth tail percentiles, liquidation probabilities, and volatility to set reference exit levels; exit when **realized price** hits those levels |

---

## Part 1: Announcement / Entry Point

### API Endpoints Used

| Endpoint | Data Provided | Role for Entry |
|----------|---------------|----------------|
| `/prediction-percentiles` | 9 percentile price levels (0.5th → 99.5th), `current_price` | Define entry zones (e.g., buy near 20th, sell near 80th) |
| `/insights/lp-probabilities` | P(above X) and P(below X) at 11 upside + 11 downside levels | Entry when P crosses thresholds |
| `/insights/lp-bounds` | Price intervals + P(stay in range) + expected time in range | Range-bound entry: enter at interval edges |
| `/v2/prediction/latest` | Latest prediction rates (time series) | Confirm trend / momentum before entry |
| `/insights/polymarket/up-down/daily` | Synth P(up) vs market P(up) | Enter when Synth disagrees with market (edge signal) |

### Entry Logic Options

#### A. Percentile Zone Entry (Mean Reversion)

- **Reference from Synth:** 20th, 35th, 65th, 80th percentile price levels
- **Bullish entry:** Enter when **realized price** drops to or below Synth's 20th/35th percentile (value zone)
  - Rule: `if realized_price <= synth_percentile_20 → ENTRY_SIGNAL_long`
- **Bearish entry:** Enter when **realized price** rises to or above Synth's 80th/65th percentile (overbought)
  - Rule: `if realized_price >= synth_percentile_80 → ENTRY_SIGNAL_short`

**Source:** Synth `/prediction-percentiles` → reference levels; **realized price** from market feed → execution trigger.

---

#### B. Probability Threshold Entry (Directional)

- **Bullish entry:** Synth's P(price above target) exceeds threshold (e.g. 65%) at a target above current price  
  - Rule: `if P(above X) >= 0.65 for X > current_price → ENTRY_SIGNAL_long`
- **Bearish entry:** Synth's P(price below target) exceeds threshold at a target below current price  
  - Rule: `if P(below X) >= 0.65 for X < current_price → ENTRY_SIGNAL_short`

**Source:** `/insights/lp-probabilities` → `data.probability_above` / `probability_below`

---

#### C. Range-Bound Entry (LP Bounds)

- **Entry:** Price near the lower bound of a high-probability interval  
  - Rule: Enter long when price touches an interval's lower bound where `probability_to_stay_in_interval` is high (e.g. > 0.5) and `expected_impermanent_loss` is acceptable  
- **Exit for range:** Near upper bound (handled as take-profit, not stop-loss)

**Source:** `/insights/lp-bounds` → `data[].interval`, `probability_to_stay_in_interval`, `expected_time_in_interval`

---

#### D. Synth vs Market Divergence Entry

- **Entry:** Synth P(up) – market P(up) > threshold (e.g. 10%) → bullish edge  
- **Entry:** Market P(up) – Synth P(up) > threshold → bearish edge (fade the market)

**Source:** `/insights/polymarket/up-down/daily` → `synth_probability_up` vs `polymarket_probability_up`

---

#### E. Prediction Path Confirmation (Momentum)

- **Entry:** Use `/v2/prediction/latest` for predicted path; enter only when:
  - Price is moving in predicted direction
  - Predicted path slope and recent candles align
- Acts as a filter on top of A–D rather than a standalone signal

---

### Recommended Entry Flow

1. Poll Synth `/prediction-percentiles` and `/insights/lp-probabilities` → derive **reference levels** (e.g. percentile_20, percentile_80)
2. Stream or poll **realized price** from your market data feed
3. Check: has **realized price** reached the Synth-derived levels?
   - Percentile zone: `realized_price` vs synth 20th/80th
   - Probability: use Synth P(above/below) to validate that the level is still favorable
   - (Optional) LP bounds and Polymarket divergence for additional filters
4. Execute entry when **realized price** hits the Synth reference level (optional: confirm with `/v2/prediction/latest`)

---

## Part 2: Stop-Loss Point

### API Endpoints Used

| Endpoint | Data Provided | Role for Stop-Loss |
|----------|---------------|--------------------|
| `/prediction-percentiles` | Tail percentiles (0.5th, 5th, 95th, 99.5th) | Stop beyond "tail" of distribution |
| `/insights/liquidation` | P(liquidation) at price levels for 6h, 12h, 18h, 24h | Stop before liquidation risk spikes |
| `/insights/lp-probabilities` | P(below X) for longs, P(above X) for shorts | Stop when P(unfavorable) exceeds threshold |
| `/insights/volatility` | Forecast vol, realized vol | Adjust stop width by vol regime |
| `/insights/lp-bounds` | Probability to stay in interval | Stop when price exits high-confidence interval |

### Stop-Loss Logic Options

#### A. Percentile-Based Stop (Tail Risk)

- **Reference from Synth:** 5th, 0.5th, 95th, 99.5th percentile price levels
- **Long stop:** Reference level = Synth's 5th or 0.5th percentile; exit when **realized price** trades at or below it  
  - Rule: `STOP_LONG_REF = synth_percentile_5 * (1 - buffer)` → execute when `realized_price <= STOP_LONG_REF`
- **Short stop:** Reference level = Synth's 95th or 99.5th percentile; exit when **realized price** trades at or above it  
  - Rule: `STOP_SHORT_REF = synth_percentile_95 * (1 + buffer)` → execute when `realized_price >= STOP_SHORT_REF`

**Source:** Synth `/prediction-percentiles` → reference levels; **realized price** from market feed → execution trigger.

---

#### B. Liquidation-Aware Stop (Leveraged)

- **Long stop:** Place below the price level where `long_liquidation_probability` (e.g. 24h) exceeds max acceptable risk (e.g. 5%)  
- **Short stop:** Place above the price level where `short_liquidation_probability` exceeds threshold  

**Source:** `/insights/liquidation` → `data[].long_liquidation_probability`, `short_liquidation_probability` by price level

---

#### C. Probability-Based Stop

- **Long stop:** Exit when Synth's P(price below S) > threshold (e.g. 80%) for some level S  
  - Interpret S as "invalidated" zone; stop slightly below S  
- **Short stop:** Exit when P(price above S) > threshold for level S; stop slightly above S  

**Source:** `/insights/lp-probabilities` → iterate `probability_below` / `probability_above` to find S

---

#### D. Volatility-Adjusted Stop

- **Base stop:** From percentiles (A) or probabilities (C)
- **Widen** stop when `forecast_future.average_volatility` is high  
  - Rule: `STOP_ADJUSTED = STOP_BASE * (1 + k * vol_ratio)` where vol_ratio = forecast_vol / baseline_vol  
- **Tighten** when vol is low (optional, to lock in profits faster)

**Source:** `/insights/volatility` → `forecast_future.average_volatility`, `realized`

---

#### E. LP Bounds Exit (Range Break)

- **Stop for range trade:** Exit when price breaks below/above the interval that had high `probability_to_stay_in_interval`  
  - Long: stop below interval lower bound  
  - Short: stop above interval upper bound  

**Source:** `/insights/lp-bounds` → `data[].interval.lower_bound`, `upper_bound`

---

### Recommended Stop-Loss Flow

1. On entry, fetch Synth `/prediction-percentiles` and `/insights/liquidation` (if leveraged) → derive **reference stop levels**
2. Compute initial stop reference:
   - Long: `STOP_REF = min(synth_percentile_5, liquidation_safe_level) * (1 - buffer)`
   - Short: `STOP_REF = max(synth_percentile_95, liquidation_safe_level) * (1 + buffer)`
3. Monitor **realized price**; execute exit when `realized_price` crosses the stop reference
4. Periodically refresh Synth `/insights/volatility`; adjust stop reference per vol regime
5. Trailing option: Update stop reference as Synth percentiles evolve; always execute when **realized price** hits the updated reference

---

## Part 3: Combined Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  ENTRY (Announcement)                                            │
├─────────────────────────────────────────────────────────────────┤
│  1. Synth: GET /prediction-percentiles, /insights/lp-probabilities│
│     → Derive REFERENCE levels (entry price, e.g. percentile_20)  │
│  2. Market: Stream REALIZED PRICE                                │
│  3. When REALIZED PRICE reaches Synth reference → ENTRY_SIGNAL   │
│  4. [Optional] Confirm with /v2/prediction/latest                │
│  5. Compute initial stop REFERENCE from Synth                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  STOP-LOSS (Ongoing)                                             │
├─────────────────────────────────────────────────────────────────┤
│  1. Synth: GET /prediction-percentiles, /insights/liquidation    │
│     → Derive REFERENCE stop level (e.g. percentile_5 for long)   │
│  2. Market: Monitor REALIZED PRICE                               │
│  3. When REALIZED PRICE hits stop REFERENCE → EXECUTE EXIT       │
│  4. Periodically: Refresh Synth, adjust stop reference by vol    │
│  5. [Optional] Trail: update stop reference from new percentiles │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 4: API Call Summary

| Purpose | Primary APIs | Secondary / Optional |
|---------|--------------|----------------------|
| **Entry signal** | `/prediction-percentiles`, `/insights/lp-probabilities` | `/insights/lp-bounds`, `/insights/polymarket/up-down/daily`, `/v2/prediction/latest` |
| **Stop-loss level** | `/prediction-percentiles`, `/insights/liquidation` | `/insights/volatility`, `/insights/lp-probabilities`, `/insights/lp-bounds` |

---

## Part 5: Parameter Suggestions (Starting Points)

| Parameter | Suggested Value | Notes |
|-----------|-----------------|-------|
| Entry percentile (long) | 20th or 35th | More conservative = 35th |
| Entry percentile (short) | 65th or 80th | More conservative = 65th |
| Entry P(above/below) threshold | 0.60–0.70 | Higher = fewer but stronger signals |
| Stop percentile (long) | 5th or 0.5th | 5th = wider stop, 0.5th = tighter |
| Stop percentile (short) | 95th or 99.5th | Same logic |
| Stop buffer | 0.3%–1% | Avoid stops too close to noise |
| Liquidation P threshold | 5%–10% | Max acceptable liquidation risk |
| Poll interval | 5–15 min | Match your holding period |

---

## Part 6: Implementation Checklist

- [ ] Integrate Synth `/prediction-percentiles` and `/insights/lp-probabilities` → derive **reference** entry/stop levels  
- [ ] Integrate **realized price** feed (broker/market data API) for execution triggers  
- [ ] Ensure all entry/exit logic compares **realized price** against Synth-derived reference levels  
- [ ] Define entry rules (percentile zone, probability, or both)  
- [ ] Define stop rules (percentile-based, liquidation-aware, or both)  
- [ ] Add Synth `/insights/liquidation` for leveraged positions  
- [ ] Add vol-adjusted stop using Synth `/insights/volatility`  
- [ ] Set polling cadence for Synth (reference) and realized price (execution); add error handling  
- [ ] Backtest: use historical Synth outputs as reference levels, historical prices as realized, or paper trade  

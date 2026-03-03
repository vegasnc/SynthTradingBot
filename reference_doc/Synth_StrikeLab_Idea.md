# Synth StrikeLab – Best Options Tool Concept

A focused idea for a Best Options Tool that leverages Synth's probabilistic distributions.

---

## Idea: "Synth StrikeLab" – Probability-Weighted Options Playground

A single interface that turns Synth's distributions into actionable options signals.

### Core Concept

Instead of just showing strikes, define **"Synth Key Strikes"** – strike levels aligned to Synth's percentiles (5th, 20th, 50th, 80th, 95th) where probability mass concentrates. The tool would:

- Highlight strikes where Synth sees fair value vs. mispricing
- Recommend position sizing based on probability of profit
- Compare Synth's forecast volatility vs. implied vol from the market

### Main Features

| Feature | Description |
|---------|-------------|
| **Strike Spectrum Map** | 2D heatmap: strikes on X-axis, strategies on Y-axis (buy call, buy put, sell call, sell put, vertical spreads). Color intensity = Synth's expected edge or confidence. |
| **Percentile Strike Lines** | Vertical reference lines at Synth's key percentiles (0.5th, 5th, 20th, 50th, 80th, 95th, 99.5th). These are natural strike levels; the UI explains why each matters (e.g., "5th percentile = aggressive put support"). |
| **Edge Scanner** | User inputs current market option prices; tool compares to Synth's fair values and highlights strikes where Synth implies >X% mispricing (configurable threshold). |
| **Confidence-Based Sizer** | Uses Synth's distribution to estimate P(profit) for a chosen strategy, then suggests position size from the user's risk budget. |
| **Synth vs IV Vol View** | Side-by-side: Synth forecast vol vs. implied vol (user enters or fetches from another source) for the same asset/expiry. |

### UX Hook

One click: **"Find My Best Strike"**. User selects direction (bullish / neutral / bearish) and risk tolerance. Tool returns 3–5 strike suggestions with rationale, e.g.: *"Strike $X aligns with Synth's 80th percentile; Synth fair value 12% below market → undervalued call."*

---

## Synth Endpoints to Use

| Endpoint | Role in the Tool |
|----------|------------------|
| **`/insights/option-pricing`** | Synth's theoretical call/put prices per strike – core for edge vs market. |
| **`/insights/lp-probabilities`** | P(price above/below) at 11 levels each – strike selection and P(ITM). |
| **`/prediction-percentiles`** | Full distribution (9 percentiles) – key strikes, sizers, and risk views. |
| **`/insights/volatility`** | Synth forecast vol – compare with implied vol. |
| **`/insights/lp-bounds`** | Intervals + probability of staying in range – butterflies, iron condors. |

Optional:

- **`/insights/liquidation`** – flag strikes near liquidation zones for leveraged instruments.

---

## Suggested Build Order

1. **Phase 1:** Asset selector + `prediction-percentiles` + `lp-probabilities` → Percentile Strike Lines + basic "Best Strike" suggestions.
2. **Phase 2:** `option-pricing` → Edge Scanner vs market prices.
3. **Phase 3:** Position sizer using P(profit) from the distribution.
4. **Phase 4:** `volatility` + IV input → Vol comparison panel.
5. **Phase 5:** `lp-bounds` → Range strategies and Strike Spectrum Map.

This keeps the tool lean while making Synth's distribution and option pricing the main value.

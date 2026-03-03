# Synth BTC Price Prediction App – Knowledge Base

This document summarizes the analysis of the Synth subnet and data sources for building a **BTC price prediction app** in Python.

---

## 1. Synth Subnet (Bittensor SN50)

- **What it is:** A decentralized subnet that produces **synthetic probabilistic price-path data** (Monte Carlo–style) for crypto (and later equities). Miners submit many simulated price paths; validators score them with **CRPS** (Continuous Ranked Probability Score) against **real prices**.
- **Task (Phase 1):** 100/1000 paths for BTC (and ETH, SOL, XAU, tokenized equities), 5‑minute increments, 24 hours ahead. Also 1‑hour HFT predictions.
- **Sources:** [Whitepaper PDF](https://mode-network.github.io/synth-subnet/Synth%20Whitepaper%20v1.pdf), [GitHub](https://github.com/mode-network/synth-subnet), [Synth website](https://www.synthdata.co), [Paragraph article](https://paragraph.com/@synthdata/synth-subnet-inside-synth-s-accuracy-surge).

---

## 2. Data Sources for the App

### 2.1 Real BTC price (ground truth)

- **Provider:** **Pyth Network** (recommended in the Synth whitepaper for validators).
- **API:** Hermes – public REST, no API key required.
  - Base: `https://hermes.pyth.network`
  - Latest price: `GET /v2/updates/price/latest?ids[]=<FEED_ID>`
  - BTC/USD feed ID (hex): `0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43`
  - Response: `parsed[].price` has `price` (integer), `expo` (e.g. -8) → **actual price = price × 10^expo**
- **Historical:** Pyth Benchmarks – `https://benchmarks.pyth.network` (see [Historical Price Data](https://docs.pyth.network/price-feeds/core/use-historical-price-data)).
- **Docs:** [Pyth API Reference](https://docs.pyth.network/price-feeds/core/api-reference), [Hermes](https://hermes.pyth.network/docs).

### 2.2 Synth predictions (synthetic price paths)

- **Provider:** **Synth API** (Synth Data).
- **Base URLs:**
  - Production: `https://api.synthdata.co`
  - Testnet: `https://api-testnet.synthdata.co`
- **Auth:** Prediction and insights endpoints require header:  
  `Authorization: Apikey <YOUR_API_KEY>`  
  (API key from [dashboard.synthdata.co](https://dashboard.synthdata.co/login)).
- **Prediction endpoints (V2):**
  - **Best miner:**  
    `GET /v2/prediction/best?asset=BTC&time_increment=300&time_length=86400`
  - **Latest (multiple miners):**  
    `GET /v2/prediction/latest?asset=BTC&time_increment=300&time_length=86400&limit=10`
  - **Historical:**  
    `GET /v2/prediction/historical?miner[]=1&asset=BTC&start_time=<ISO8601>&time_increment=300&time_length=86400`
- **Parameters:**
  - `asset`: e.g. `BTC`, `ETH`, `SOL`, `XAU`, …
  - `time_increment`: `300` (5 min) or `60` (1 min).
  - `time_length`: `86400` (24 h) or `3600` (1 h).
  - `prompt_name`: for leaderboard – `low` (24h), `high` (1h).
- **Response shape (conceptually):** `miner_uid`, `predictions` (list of paths; each path is list of prices or [time, price]), `start_time`.
- **Other useful endpoints (leaderboard/validation):**  
  `/v2/leaderboard/latest`, `/v2/leaderboard/historical`, `/v2/validation/scores/latest`, `/v2/validation/scores/historical` – may work without key for read-only; confirm in [API docs](https://api.synthdata.co/docs).

---

## 3. Implementation Outline (Python)

1. **Environment:** Python 3.x; `requests` for HTTP; optional `pandas`/`matplotlib`/`plotly` for analysis/plots.
2. **Pyth:**  
   - Call `GET https://hermes.pyth.network/v2/updates/price/latest?ids[]=0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43`, parse `parsed[0].price.price` and `expo` to get current BTC price.
3. **Synth:**  
   - Set `Authorization: Apikey <KEY>`; call `/v2/prediction/best` or `/v2/prediction/latest` for BTC 24h (300/86400); parse `predictions` and `start_time`.
4. **App:**  
   - CLI and/or simple web UI (e.g. Streamlit): show current BTC price (Pyth) and Synth prediction paths (e.g. percentiles over time); optionally compare with realized price after 24h using Pyth historical or Synth historical predictions.

---

## 4. References

| Resource | URL |
|----------|-----|
| Synth Whitepaper | https://mode-network.github.io/synth-subnet/Synth%20Whitepaper%20v1.pdf |
| Synth GitHub | https://github.com/mode-network/synth-subnet |
| Synth website | https://www.synthdata.co |
| Synth API docs (prod) | https://api.synthdata.co/docs |
| Synth API docs (testnet) | https://api-testnet.synthdata.co/docs |
| Pyth Hermes API | https://hermes.pyth.network/docs |
| Pyth Price Feeds / Feed IDs | https://docs.pyth.network/price-feeds/core/price-feeds |

---

*Last updated from analysis of the three provided URLs and related docs. Ready for implementation.*

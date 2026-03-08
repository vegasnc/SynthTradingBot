# Synth Multi-Asset Trading Bot (Engine + Dashboard)

Two-app production-oriented system:

- `engine/` (Python + FastAPI): strategy, Synth integration, scheduler, paper broker, Mongo persistence.
- `dashboard/` (React + Vite): monitoring UI for signals, positions, orders, and controls.

## Safety Defaults

- Paper trading is enabled by default.
- Live trading must be explicitly enabled via environment/config.
- Equities are skipped outside market hours and during first 15 minutes after open.

## 1) Setup

### Prerequisites

- Python 3.11+
- Node 20+
- MongoDB Atlas URI
- Synth API key

### Environment

Copy `engine/.env.example` to `engine/.env` and set:

- `SYNTH_API_KEY`
- `MONGO_URI`
- `BROKER_API_KEY_CRYPTO`, `BROKER_API_SECRET_CRYPTO`
- `BROKER_API_KEY_EQUITY`, `BROKER_API_SECRET_EQUITY`
- `PAPER_TRADING=true`
- `SYMBOLS=BTC-USD:crypto,ETH-USD:crypto,SPY:equity,AAPL:equity`
**Market data flow:** The **engine** fetches crypto prices (Binance/Kraken) server-side. Equity prices come from the dashboard via `POST /prices`. No Finnhub or equity API key required.
`synth_api_endpoints.json` is loaded from repo root as required.

## 2) Run Engine

```bash
cd engine
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn run_engine:app --reload --port 8000
```

Scheduler runs inside FastAPI lifespan and executes every minute (`ENGINE_LOOP_SECONDS` configurable).

## 3) Run Dashboard

```bash
cd dashboard
npm install
npm run dev
```

Dashboard URL: `http://localhost:5173`

## 4) Data Model (MongoDB Atlas)

Collections:

- `synth_predictions`
- `candles_1m`
- `candles_5m`
- `signals`
- `orders`
- `positions`
- `events`

## 5) FastAPI Endpoints

- `GET /health`
- `GET /symbols`
- `GET /state`
- `GET /predictions?symbol=`
- `GET /signals?symbol=&limit=`
- `GET /positions`
- `GET /orders?symbol=&limit=`
- `GET /candles?symbol=&timeframe=1m|5m&limit=`
- `POST /controls`
- `WS /stream`

## 6) Strategy Rules Implemented

- Synth 1H percentiles drive all decisions.
- Hybrid timing: 1m trigger + 5m confirmation with VWAP.
- Trade quality filters:
  - `abs(Edge) >= 0.18 * Uncertainty`
  - uncertainty caps by market type
  - cooldown after stop-out (30 min)
- Stops/TPs differ for crypto vs equities.
- TP1/TP2 partial exits + tighten-only trailing.
- Risk-based position sizing with symbol and portfolio exposure caps.
- Adaptive Synth refresh cadence (20/15/10/5 min, with 5m only for high uncertainty crypto).

## 7) Tests and Backtest

```bash
cd engine
python -m pytest -q
python run_backtest.py
```

Unit tests cover:

- percentile parsing
- trade filters
- entry/stop/TP math
- tighten-only trailing logic
- position sizing

## 8) Notes

- Paper broker is implemented and wired end-to-end.
- Live broker adapters are abstracted (`CryptoBroker`, `EquityBroker`) and ready for concrete API integrations.
- Liquidation stop adjustment is optional and best-effort when endpoint payload provides price/probability bands.

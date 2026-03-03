# Synth Multi-Asset Trading Bot

Production-grade Python trading bot using Synth probabilistic forecasts across:

- Crypto: Binance Spot + Binance USDT-M Perps (`BTC/ETH/SOL`)
- Equities/ETF: Alpaca Spot (`SPY/AAPL/NVDA/TSLA/GOOGL`)
- XAU: Synth forecast on `XAU`, execution configurable as:
  - `forecast_only`, or
  - ETF proxy (default `GLD`)

## Architecture

```
bot/
  config/
  synth/
  marketdata/
  indicators/
  regime/
  strategy/
  portfolio/
  risk/
  execution/
    binance/
    alpaca/
  backtest/
  utils/
  main.py
tests/
```

## Persistence (SQLite)

The bot now persists runtime state in `bot_data.db`:

- `positions` (open + closed lifecycle; survives restart)
- `trades` (entry/exit, reason, pnl)
- `decisions` (structured decision snapshot JSON)
- `synth_snapshots` (forecast snapshots for audit/backtest parity)
- `state` (`equity`, `peak_equity`, day reset and kill-switch flags)

## Core Design

- **Entries/SL/TP** derived from Synth percentiles (`p20/p5`, `p80/p95`) only.
- **Portfolio allocation** via risk parity on Synth volatility with hard caps:
  - single asset <= 15%
  - crypto bucket <= 30%
  - tech single-name bucket <= 40%
  - per trade <= 25% of asset allocation
- **Perps guardrail**: liquidation probability at/near stop must be <= 1%.
- **Regime selection**: one unified engine, regime switches TREND vs RANGE (no conflicting strategies).
- **Kill switches**:
  - daily loss > 2%: stop trading for day
  - drawdown > 8%: halve risk
  - drawdown > 15%: lock trading (manual restart/reset)
- **Decision Snapshot JSON logging** emitted every tick.

## Strategy Rules (implemented)

Features:

- `mu = (p50 - spot) / spot`
- `spread = (p95 - p5) / spot`
- `skew = (p50 - spot) / (p95 - p5)`

Regime:

- `TREND` if `abs(EMA20-EMA50)/ATR14 > threshold` and price aligned
- `RANGE` otherwise
- `EXTREME_VOL` if Synth spread or Synth vol exceeds threshold
- `NO_TRADE` for invalid inputs

Signals:

- TREND long: `mu>0 && skew>0.15`
- TREND short: `mu<0 && skew<-0.15`
- RANGE long: `spot < p20 && p50 > spot`
- RANGE short: `spot > p80 && p50 < spot`

Trade gate:

- Reward/Risk must be `>= 2.0`

## Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill keys.

## Run (paper)

```bash
python -m bot.main --horizon 24h --mode paper
```

## Run (live)

```bash
python -m bot.main --horizon 1h --mode live
```

## Web Dashboard

```bash
streamlit run bot/dashboard.py
```

Dashboard includes:

- Realized candlestick chart
- Long/short entry and exit indicators
- Progressing (open) trades
- Trading history with per-trade P&L
- Total P&L, wins/losses, and win-rate
- Recent Synth snapshot diagnostics

## Backtesting parity

`bot/backtest/engine.py` uses the same:

- regime detection
- signal engine
- risk sizing

Only execution differs (simulated fills, fees, slippage).

## Tests

```bash
pytest -q
```

## Assumptions / Notes

- Binance perps leverage starts conservative (`2x`) and can be tuned.
- Liquidation endpoint windows are coarse (6/12/18/24h). For `1h`, bot uses 6h as conservative proxy.
- XAU execution can be `forecast_only` or proxy ETF (`GLD`) while keeping Synth asset as `XAU`.


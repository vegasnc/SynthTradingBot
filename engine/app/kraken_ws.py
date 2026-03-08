"""
Kraken WebSocket real-time market data using python-kraken-sdk.
Updates EngineState with live ticker (spot) and OHLC (candles) for crypto and xStocks.
- BTC, ETH, XAU: spot + OHLC 1m/5m (unchanged).
- xStocks (GOOGLx, SPYx, NVDAx, TSLAx, AAPLx): ticker only (real-time price via Spot WS).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .state import EngineState
from .utils import utc_now

logger = logging.getLogger(__name__)

# Kraken Spot: BTC, ETH, XAU (spot + OHLC)
KRAKEN_TO_SYMBOLS: dict[str, list[str]] = {
    "BTC/USD": ["BTC", "BTC-USD"],
    "ETH/USD": ["ETH", "ETH-USD"],
    "XAU/USD": ["XAU", "XAU-USD"],
    # xStocks (perpetual futures on Kraken; real-time via Spot WebSocket, ticker only)
    "GOOGLx/USD": ["GOOGLX", "GOOGL"],
    "SPYx/USD": ["SPYX", "SPY"],
    "NVDAx/USD": ["NVDAX", "NVDA"],
    "TSLAx/USD": ["TSLAX", "TSLA"],
    "AAPLx/USD": ["AAPLX", "AAPL"],
}
# All symbols for ticker (spot + xStocks)
KRAKEN_SYMBOLS = list(KRAKEN_TO_SYMBOLS.keys())
# Only crypto for OHLC (BTC, ETH, XAU) – unchanged
KRAKEN_OHLC_SYMBOLS = ["BTC/USD", "ETH/USD", "XAU/USD"]


def _symbols_for_kraken_pair(kraken_pair: str) -> list[str]:
    return KRAKEN_TO_SYMBOLS.get(kraken_pair, [kraken_pair.replace("/", "-")])


def _market_type_for_pair(kraken_pair: str) -> str:
    """xStocks (e.g. GOOGLx/USD) are equity; BTC/USD, ETH/USD, XAU/USD are crypto."""
    base = (kraken_pair.split("/")[0] if "/" in kraken_pair else kraken_pair).strip()
    return "equity" if base.endswith("x") else "crypto"


def run_kraken_ws(state: EngineState) -> asyncio.Task[None]:
    """
    Start the Kraken WebSocket client in the background (Spot WS = wss://ws.kraken.com).
    - Ticker: all symbols (BTC, ETH, XAU + xStocks GOOGLx, SPYx, NVDAx, TSLAx, AAPLx) for real-time spot.
    - OHLC 1m/5m: BTC, ETH, XAU only (unchanged). xStocks are ticker-only (perpetual futures).
    Updates state.latest_market_data and state.latest_price. No API key for public feeds.
    Returns the asyncio Task so the caller can cancel it on shutdown.
    """
    try:
        from kraken.spot import SpotWSClient
    except ImportError:
        logger.warning("python-kraken-sdk not installed; Kraken WebSocket disabled. pip install python-kraken-sdk")
        return asyncio.create_task(_noop_ws_loop())

    async def on_message(message: dict[str, Any]) -> None:
        if message.get("method") == "pong" or message.get("channel") == "heartbeat":
            return
        channel = message.get("channel")
        msg_type = message.get("type")
        data = message.get("data") or []

        if channel == "ticker" and data:
            # data: list of one ticker object: { symbol, last, bid, ask, ... } (BTC/ETH/XAU + xStocks)
            for item in data:
                if not isinstance(item, dict):
                    continue
                pair = item.get("symbol") or ""
                last = item.get("last")
                if last is None:
                    continue
                try:
                    spot = float(last)
                except (TypeError, ValueError):
                    continue
                mkt = _market_type_for_pair(pair)
                for sym in _symbols_for_kraken_pair(pair):
                    existing = state.latest_market_data.get(sym) or {}
                    state.latest_market_data[sym] = {
                        **existing,
                        "spot": spot,
                        "received_at": utc_now(),
                        "market_type": mkt,
                        "candles_1m": existing.get("candles_1m") or [],
                        "candles_5m": existing.get("candles_5m") or [],
                    }
                    state.latest_price[sym] = spot

        if channel == "ohlc" and data:
            for item in data:
                if not isinstance(item, dict):
                    continue
                pair = item.get("symbol") or ""
                interval = item.get("interval")
                try:
                    ts_str = item.get("interval_begin") or item.get("timestamp") or ""
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                    else:
                        ts = utc_now()
                except Exception:
                    ts = utc_now()
                try:
                    open_ = float(item.get("open", 0))
                    high = float(item.get("high", 0))
                    low = float(item.get("low", 0))
                    close = float(item.get("close", 0))
                    volume = float(item.get("volume", 0))
                    vwap = item.get("vwap")
                    vwap_f = float(vwap) if vwap is not None else (high + low + close) / 3
                except (TypeError, ValueError):
                    continue
                candle = {
                    "ts": ts,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "vwap": vwap_f,
                }
                # OHLC only for BTC, ETH, XAU (crypto); xStocks get ticker only
                for sym in _symbols_for_kraken_pair(pair):
                    existing = state.latest_market_data.get(sym) or {}
                    c1 = list(existing.get("candles_1m") or [])
                    c5 = list(existing.get("candles_5m") or [])
                    if interval == 1:
                        _append_candle(c1, candle, 100)
                    elif interval == 5:
                        _append_candle(c5, candle, 72)
                    state.latest_market_data[sym] = {
                        **existing,
                        "spot": existing.get("spot") or close,
                        "candles_1m": c1,
                        "candles_5m": c5,
                        "received_at": utc_now(),
                        "market_type": existing.get("market_type") or "crypto",
                    }
                    state.latest_price[sym] = state.latest_market_data[sym]["spot"]

    async def run_ws() -> None:
        from kraken.spot import SpotWSClient

        client: SpotWSClient | None = None
        while True:
            try:
                client = SpotWSClient(callback=on_message)
                await client.start()
                await client.subscribe(params={"channel": "ticker", "symbol": KRAKEN_SYMBOLS})
                await client.subscribe(
                    params={"channel": "ohlc", "interval": 1, "snapshot": True, "symbol": KRAKEN_OHLC_SYMBOLS}
                )
                await client.subscribe(
                    params={"channel": "ohlc", "interval": 5, "snapshot": True, "symbol": KRAKEN_OHLC_SYMBOLS}
                )
                logger.info(
                    "Kraken WebSocket connected; ticker=%s ohlc-1/5=%s",
                    KRAKEN_SYMBOLS,
                    KRAKEN_OHLC_SYMBOLS,
                )
                while not getattr(client, "exception_occur", False):
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Kraken WebSocket error: %s; reconnecting in 15s", e)
                await asyncio.sleep(15)
            finally:
                if client:
                    try:
                        await client.close()
                    except Exception:
                        pass
                    client = None

    return asyncio.create_task(run_ws())


def _append_candle(candles: list[dict], candle: dict, max_len: int) -> None:
    ts = candle.get("ts")
    if not ts:
        return
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    for i, c in enumerate(candles):
        cts = c.get("ts")
        if cts is None:
            continue
        if isinstance(cts, datetime) and cts.tzinfo is None:
            cts = cts.replace(tzinfo=timezone.utc)
        if cts == ts:
            candles[i] = candle
            return
    candles.append(candle)
    candles.sort(key=lambda x: x.get("ts") or datetime.min.replace(tzinfo=timezone.utc))
    if len(candles) > max_len:
        del candles[: len(candles) - max_len]


async def _noop_ws_loop() -> None:
    while True:
        await asyncio.sleep(3600)

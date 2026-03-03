"""
Fetch crypto market data server-side (avoids browser CORS and Binance 451).
Uses Binance first; falls back to Kraken if Binance is blocked (e.g. 451).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import SymbolConfig
from .utils import utc_now

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"
KRAKEN_BASE = "https://api.kraken.com/0/public"

KRAKEN_PAIRS = {"BTC": "XXBTZUSD", "ETH": "XETHZUSD", "BTC-USD": "XXBTZUSD", "ETH-USD": "XETHZUSD"}


def _to_binance_pair(symbol: str) -> str:
    base = symbol.replace("-USD", "").replace("-", "").upper()
    return f"{base}USDT" if not base.endswith("USDT") else base


def _to_kraken_pair(symbol: str) -> str | None:
    base = symbol.replace("-USD", "").replace("-", "").upper()
    return KRAKEN_PAIRS.get(base) or KRAKEN_PAIRS.get(symbol)


def _parse_binance_row(row: list) -> dict[str, Any]:
    return {
        "ts": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
    }


def _parse_kraken_row(row: list) -> dict[str, Any]:
    return {
        "ts": datetime.fromtimestamp(row[0], tz=timezone.utc),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[6]),
    }


def _compute_vwap(candles: list[dict]) -> float:
    total = 0.0
    den = 0.0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0) or 0
        total += typical * vol
        den += vol
    return total / den if den > 0 else (candles[-1]["close"] if candles else 0)


async def _fetch_binance(client: httpx.AsyncClient, pair: str, interval: str, limit: int) -> list[dict] | None:
    try:
        resp = await client.get(
            f"{BINANCE_BASE}/klines",
            params={"symbol": pair, "interval": interval, "limit": limit},
            timeout=10.0,
        )
        if resp.status_code == 451:
            logger.warning("Binance returned 451 (geo-blocked), will try Kraken")
            return None
        resp.raise_for_status()
        rows = resp.json()
        return [_parse_binance_row(r) for r in rows]
    except httpx.HTTPError as e:
        logger.warning("Binance fetch failed for %s: %s", pair, e)
        return None


async def _fetch_kraken(client: httpx.AsyncClient, pair: str, interval: int, limit: int) -> list[dict] | None:
    try:
        resp = await client.get(
            f"{KRAKEN_BASE}/OHLC",
            params={"pair": pair, "interval": interval},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            logger.warning("Kraken error: %s", data["error"])
            return None
        result = data.get("result", {})
        key = next((k for k in result if k != "last"), None)
        if not key:
            return None
        rows = result[key][-limit:]
        return [_parse_kraken_row(r) for r in rows]
    except httpx.HTTPError as e:
        logger.warning("Kraken fetch failed for %s: %s", pair, e)
        return None


async def fetch_crypto_prices(
    client: httpx.AsyncClient,
    symbols: list[SymbolConfig],
) -> dict[str, dict[str, Any]]:
    """Fetch market data for crypto symbols. Returns {symbol: {spot, candles_1m, candles_5m, ...}}."""
    out: dict[str, dict[str, Any]] = {}
    for cfg in symbols:
        if cfg.market_type != "crypto":
            continue
        symbol = cfg.symbol

        candles_1m: list[dict] | None = None
        candles_5m: list[dict] | None = None

        binance_pair = _to_binance_pair(symbol)
        candles_1m = await _fetch_binance(client, binance_pair, "1m", 50)
        candles_5m = await _fetch_binance(client, binance_pair, "5m", 24)

        if candles_1m is None or candles_5m is None:
            kraken_pair = _to_kraken_pair(symbol)
            if kraken_pair:
                if candles_1m is None:
                    candles_1m = await _fetch_kraken(client, kraken_pair, 1, 50)
                if candles_5m is None:
                    candles_5m = await _fetch_kraken(client, kraken_pair, 5, 24)

        if candles_1m and candles_5m:
            spot = candles_1m[-1]["close"]
            last_5 = candles_5m[-12:]
            vwap = _compute_vwap(last_5)
            if last_5:
                last_5[-1] = {**last_5[-1], "vwap": vwap}
            out[symbol] = {
                "spot": spot,
                "candles_1m": candles_1m,
                "candles_5m": candles_5m,
                "received_at": utc_now(),
                "market_type": cfg.market_type,
            }
        else:
            logger.warning("No market data for %s (Binance and Kraken failed)", symbol)
    return out

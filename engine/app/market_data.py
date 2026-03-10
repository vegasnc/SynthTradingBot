"""
Fetch crypto market data server-side (avoids browser CORS and Binance 451).
Crypto: Binance/Kraken. Equity: Yahoo Finance chart API (no key required).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import SymbolConfig
from .utils import utc_now

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"
KRAKEN_BASE = "https://api.kraken.com/0/public"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"

KRAKEN_PAIRS = {
    "BTC": "XXBTZUSD",
    "ETH": "XETHZUSD",
    "XAU": "XAUTUSD",
    "SOL": "SOLUSD",
    "JITOSOL": "JITOSOLUSD",
    "BTC-USD": "XXBTZUSD",
    "ETH-USD": "XETHZUSD",
    "XAU-USD": "XAUTUSD",
    "SOL-USD": "SOLUSD",
    "JITOSOL-USD": "JITOSOLUSD",
}


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
        kraken_pair = _to_kraken_pair(symbol)
        # Try Kraken first (works in US); fall back to Binance (may return 451 geo-block)
        # XAU (gold) only on Kraken (XAUT); skip Binance for XAU
        if kraken_pair:
            candles_1m = await _fetch_kraken(client, kraken_pair, 1, 50)
            candles_5m = await _fetch_kraken(client, kraken_pair, 5, 72)
        if (candles_1m is None or candles_5m is None) and symbol != "XAU":
            candles_1m = candles_1m or await _fetch_binance(client, binance_pair, "1m", 50)
            candles_5m = candles_5m or await _fetch_binance(client, binance_pair, "5m", 72)

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


async def _fetch_yahoo_equity_spot(client: httpx.AsyncClient, symbol: str) -> float | None:
    """Fetch equity spot price via Yahoo Finance chart API. No API key required."""
    try:
        resp = await client.get(
            f"{YAHOO_CHART}/{symbol}",
            params={"interval": "1d", "range": "1d"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        chart = data.get("chart", {})
        result = chart.get("result")
        if not result or not isinstance(result, list):
            return None
        meta = result[0].get("meta") if isinstance(result[0], dict) else None
        if not meta:
            return None
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price is not None:
            return float(price)
        return None
    except Exception:
        return None


async def fetch_spot_prices(
    client: httpx.AsyncClient,
    symbols: list[SymbolConfig],
) -> dict[str, float]:
    """Fetch current spot prices (crypto: Binance/Kraken, equity: Yahoo Finance)."""
    out: dict[str, float] = {}
    for cfg in symbols:
        symbol = cfg.symbol
        if cfg.market_type == "crypto":
            binance_pair = _to_binance_pair(symbol)
            kraken_pair = _to_kraken_pair(symbol)
            price: float | None = None
            if kraken_pair:
                try:
                    resp = await client.get(
                        f"{KRAKEN_BASE}/Ticker",
                        params={"pair": kraken_pair},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if not data.get("error"):
                            result = data.get("result", {})
                            pair_data = result.get(kraken_pair) or (list(result.values())[0] if result else None)
                            if pair_data and isinstance(pair_data, dict):
                                c = pair_data.get("c")
                                if isinstance(c, (list, tuple)) and c:
                                    price = float(c[0])
                except Exception:
                    pass
            if price is None:
                try:
                    resp = await client.get(
                        f"{BINANCE_BASE}/ticker/price",
                        params={"symbol": binance_pair},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        price = float(data.get("price", 0) or 0)
                except Exception:
                    pass
        else:
            price = await _fetch_yahoo_equity_spot(client, symbol)
        if price and price > 0:
            out[symbol] = price
    return out

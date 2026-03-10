"""Strike portfolio allocation: Synth-based weight suggestions per asset."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.strategy import compute_edge_score

logger = logging.getLogger(__name__)


# Kraken xStocks -> Synth API asset (Synth uses GOOGL, SPY, etc.; Kraken uses GOOGLX, SPYX, etc.)
_XSTOCK_TO_SYNTH: dict[str, str] = {
    "GOOGLX": "GOOGL", "SPYX": "SPY", "TSLAX": "TSLA", "NVDAX": "NVDA", "AAPLX": "AAPL",
}


def _synth_asset(symbol: str, synth_asset_map: dict[str, str] | None = None) -> str:
    """Map trading symbol to Synth API asset. Uses SYNTH_ASSET_MAP when provided; fallback for xStocks."""
    if synth_asset_map and symbol in synth_asset_map:
        return synth_asset_map[symbol]
    if symbol in _XSTOCK_TO_SYNTH:
        return _XSTOCK_TO_SYNTH[symbol]
    if symbol.endswith("-USD"):
        return symbol.split("-")[0]
    return symbol


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


async def compute_strike_allocations(
    synth,
    store,
    state,
    strike_symbols: list[str],
    max_weight_per_asset: float = 0.30,
    max_total_crypto: float = 0.60,
    synth_asset_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Compute allocation weights for strike symbols using Synth predictions.
    Tries live Synth API first; if that fails, falls back to the latest cached
    prediction in Mongo so that assets like SOL/JITOSOL still get strike weights
    when the engine has been using Synth successfully elsewhere.
    """
    raw_scores: dict[str, float] = {}
    raw_data: dict[str, dict] = {}

    for sym in strike_symbols:
        asset = _synth_asset(sym, synth_asset_map)
        # Synth API supports only 24h horizon for xStocks (equity)
        is_equity = sym in _XSTOCK_TO_SYNTH or (synth_asset_map and sym in synth_asset_map)
        horizon = "24h" if is_equity else "1h"
        try:
            payload = await synth.get_prediction_percentiles(asset=asset, horizon=horizon)
            pct = synth.parse_percentiles(payload)
        except Exception as e:
            # If live Synth call fails (rate limit, asset name mismatch, etc.),
            # fall back to the latest cached prediction used by the engine for signals.
            logger.warning("Strike: failed to get percentiles for %s: %s; trying cached prediction", asset, e)
            try:
                cached = await store.db.synth_predictions.find_one(
                    {"symbol": sym}, sort=[("timestamp", -1)]
                )
            except Exception as e2:
                logger.warning("Strike: failed to load cached prediction for %s: %s", sym, e2)
                continue
            if not cached:
                continue
            try:
                pct = synth.parse_percentiles({"percentiles": cached.get("percentiles") or {}})
            except Exception as e3:
                logger.warning("Strike: failed to parse cached percentiles for %s: %s", sym, e3)
                continue

        # Fetch liquidation for edge score (24h for xStocks, 1h for crypto)
        liq_payload: dict[str, Any] | None = None
        try:
            liq_payload = await synth.get_liquidation_insight(asset=asset, horizon=horizon)
        except Exception:
            pass

        spot = state.latest_price.get(sym) or state.latest_price.get(f"{sym}-USD")
        if not spot and state.latest_market_data:
            mkt = state.latest_market_data.get(sym) or state.latest_market_data.get(f"{sym}-USD")
            if mkt:
                spot = float(mkt.get("spot", 0) or 0)
            if not spot and mkt:
                candles = mkt.get("candles_1m") or mkt.get("candles_5m") or []
                if candles:
                    spot = float(candles[-1].get("close", 0))
        if not spot or spot <= 0:
            # Fallback: use p50 as proxy for spot
            spot = pct.p50
        if spot <= 0:
            continue

        edge = (pct.p50 - spot) / spot
        uncertainty = (pct.p95 - pct.p05) / spot
        central_range = pct.p80 - pct.p20
        bias = "long" if edge > 0 else ("short" if edge < 0 else "neutral")
        # Edge Score = directional_signal + volatility_mispricing + liquidity_score - decay_risk - liquidation_risk
        if bias == "neutral":
            edge_score = max(
                compute_edge_score(spot, pct, "long", liq_payload),
                compute_edge_score(spot, pct, "short", liq_payload),
            )
        else:
            edge_score = compute_edge_score(spot, pct, bias, liq_payload)
        score = max(edge_score, abs(edge) / max(uncertainty, 0.000001))
        raw_weight = max(score - 0.25, 0.0)

        raw_scores[sym] = raw_weight
        raw_data[sym] = {
            "edge": edge,
            "uncertainty": uncertainty,
            "central_range": central_range,
            "score": score,
            "edge_score": edge_score,
            "bias": bias,
        }

    if not raw_scores:
        return {"allocations": {}, "timestamp": datetime.now(timezone.utc).isoformat()}

    total_raw = sum(raw_scores.values())
    if total_raw <= 0:
        weights = {s: 0.0 for s in raw_scores}
    else:
        weights = {s: raw_scores[s] / total_raw for s in raw_scores}

    # Apply max_weight_per_asset
    for s in weights:
        weights[s] = min(weights[s], max_weight_per_asset)

    # Re-normalize
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {s: weights[s] / total_w for s in weights}

    # Apply max_total_crypto for known crypto assets (include SOL and JITOSOL)
    base_crypto = {"BTC", "ETH", "SOL", "JITOSOL"}
    crypto_symbols = {s for s in weights if s in base_crypto or (s.endswith("-USD") and s.split("-")[0] in base_crypto)}
    total_crypto = sum(weights.get(s, 0) for s in crypto_symbols)
    if total_crypto > max_total_crypto:
        scale = max_total_crypto / total_crypto
        for s in crypto_symbols:
            weights[s] = weights.get(s, 0) * scale
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {s: weights[s] / total_w for s in weights}

    # Build allocations with confidence (after smoothing and final normalization)
    allocations: dict[str, dict] = {}
    prev_snapshot = await store.db.strike_snapshots.find_one(
        {}, sort=[("timestamp", -1)], projection={"allocations": 1}
    )
    prev_alloc = (prev_snapshot or {}).get("allocations") or {}

    # First compute smoothed weights, then renormalize so they sum to 1.0
    smoothed_weights: dict[str, float] = {}
    for sym in raw_data:
        rw = weights.get(sym, 0.0)
        prev_w = float((prev_alloc.get(sym) or {}).get("weight", 0.0))
        smoothed_weights[sym] = 0.7 * prev_w + 0.3 * rw

    total_sw = sum(smoothed_weights.values())
    if total_sw > 0:
        for sym in smoothed_weights:
            smoothed_weights[sym] /= total_sw

    for sym, rd in raw_data.items():
        w = smoothed_weights.get(sym, 0.0)
        confidence = _clamp(int(rd["score"] * 40), 0, 100)
        if rd["uncertainty"] > 0.08:
            confidence = max(0, confidence - 30)
        elif rd["uncertainty"] > 0.05:
            confidence = max(0, confidence - 15)

        allocations[sym] = {
            "weight": round(w, 4),
            "confidence": confidence,
            "bias": rd["bias"],
            "edge": round(rd["edge"], 4),
            "edge_score": round(rd.get("edge_score", 0), 4),
            "uncertainty": round(rd["uncertainty"], 4),
        }

    return {
        "allocations": allocations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run_strike_refresh(
    synth,
    store,
    state,
    strike_symbols_str: str,
    synth_asset_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run strike computation and store snapshot."""
    symbols = [s.strip() for s in strike_symbols_str.split(",") if s.strip()]
    if not symbols:
        symbols = ["BTC", "ETH", "XAU", "GOOGLX", "SPYX", "TSLAX", "NVDAX", "AAPLX"]

    result = await compute_strike_allocations(
        synth, store, state, symbols,
        max_weight_per_asset=0.30,
        max_total_crypto=0.60,
        synth_asset_map=synth_asset_map,
    )
    has_equity = any(s in _XSTOCK_TO_SYNTH or (synth_asset_map and s in synth_asset_map) for s in symbols)
    doc = {
        "timestamp": datetime.now(timezone.utc),
        "horizon": "mixed" if has_equity else "1h",
        "allocations": result["allocations"],
    }
    await store.db.strike_snapshots.insert_one(doc)
    logger.info("Strike snapshot stored with %d allocations", len(result["allocations"]))
    return result

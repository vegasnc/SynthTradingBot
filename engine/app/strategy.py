from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import Percentiles

Bias = Literal["long", "short", "flat"]
MarketType = Literal["crypto", "equity"]


def market_direction_from_momentum(
    spot: float,
    candles_5m: list[dict],
    lookback_minutes: int = 120,
) -> Literal["bullish", "bearish", "neutral"]:
    """
    Derive market direction from realized momentum over a lookback period.
    Bullish = price went up over the interval. Bearish = price went down.
    Uses 5m candles: lookback_minutes/5 candles ago.
    """
    if not candles_5m or lookback_minutes <= 0:
        return "neutral"
    bars_needed = max(1, lookback_minutes // 5)
    if len(candles_5m) < bars_needed:
        return "neutral"
    price_ago = float(candles_5m[-bars_needed].get("close") or candles_5m[-bars_needed].get("open", spot))
    if price_ago <= 0:
        return "neutral"
    pct_change = (spot - price_ago) / price_ago
    if pct_change > 0.001:
        return "bullish"
    if pct_change < -0.001:
        return "bearish"
    return "neutral"

# Counter-trend trades need this much higher edge than direction-aligned to override market strength
COUNTER_TREND_EDGE_MULTIPLIER = 1.5

# Minimum edge as fraction of uncertainty (stricter = fewer but higher-quality signals)
EDGE_UNCERTAINTY_MIN_FRAC = 0.15

# Minimum stop distance from entry (fraction of spot) to avoid being stopped out by noise
MIN_STOP_DISTANCE_PCT = 0.006

# Minimum risk:reward (reward/risk) to open a trade
MIN_RISK_REWARD = 1.2


@dataclass(slots=True)
class Decision:
    bias: Bias
    edge: float
    uncertainty: float
    allowed_to_trade: bool
    reasons: list[str]
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp: float
    central_range: float
    range_: float
    flags: dict[str, bool]


def _build_decision_for_bias(
    spot: float, pct: Percentiles, market_type: MarketType, bias: Literal["long", "short"]
) -> Decision:
    """Build a Decision for a specific direction (long or short)."""
    range_ = pct.p95 - pct.p05
    central_range = pct.p80 - pct.p20
    uncertainty = range_ / spot
    unc_threshold = 0.08 if market_type == "crypto" else 0.05
    reasons: list[str] = []
    flags = {"uncertainty_filter_pass": uncertainty <= unc_threshold}
    if not flags["uncertainty_filter_pass"]:
        reasons.append("uncertainty_too_high")

    if bias == "long":
        edge = (pct.p50 - spot) / spot
        tp1 = (pct.p50 + pct.p65) / 2
        tp2 = pct.p65
        entry = max(pct.p35, spot - 0.20 * central_range)
        entry = min(entry, tp1 - 0.002 * spot)
        stop_base = (pct.p05 + pct.p20) / 2
        stop = stop_base - (0.08 * central_range)
    else:
        edge = (spot - pct.p50) / spot
        tp1 = (pct.p35 + pct.p50) / 2
        tp2 = pct.p35
        entry = min(pct.p65, spot + 0.20 * central_range)
        entry = max(entry, tp1 + 0.002 * spot)
        stop_base = (pct.p80 + pct.p95) / 2
        stop = stop_base + (0.08 * central_range)
    flags["edge_filter_pass"] = abs(edge) >= EDGE_UNCERTAINTY_MIN_FRAC * uncertainty
    if not flags["edge_filter_pass"]:
        reasons.append("edge_below_threshold")
    allowed = all(flags.values())
    tp = (tp1 + tp2) / 2
    return Decision(
        bias=bias,
        edge=edge,
        uncertainty=uncertainty,
        allowed_to_trade=allowed,
        reasons=reasons.copy(),
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp=tp,
        central_range=central_range,
        range_=range_,
        flags=flags,
    )


def build_decision_with_market_strength(
    spot: float,
    pct: Percentiles,
    market_type: MarketType,
    counter_trend_multiplier: float = COUNTER_TREND_EDGE_MULTIPLIER,
    market_direction_override: Literal["bullish", "bearish", "neutral"] | None = None,
) -> Decision:
    """
    Build decision applying market-strength bias: prefer trades aligned with market direction.
    Uses market_direction_override (from realized momentum over lookback) when provided.
    Otherwise falls back to p50 vs spot (forward forecast).
    """
    if pct.p50 == spot:
        flat_d = _build_decision_for_bias(spot, pct, market_type, "long")
        flat_d.bias = "flat"  # type: ignore[misc]
        flat_d.allowed_to_trade = False
        flat_d.reasons.append("no_direction")
        flat_d.flags["market_direction"] = market_direction_override or "neutral"
        flat_d.flags["direction_aligned"] = True
        return flat_d
    if market_direction_override and market_direction_override != "neutral":
        market_bullish = market_direction_override == "bullish"
    else:
        market_bullish = pct.p50 > spot
    long_d = _build_decision_for_bias(spot, pct, market_type, "long")
    short_d = _build_decision_for_bias(spot, pct, market_type, "short")
    long_edge_abs = abs(long_d.edge)
    short_edge_abs = abs(short_d.edge)

    if market_bullish:
        aligned_d, counter_d = long_d, short_d
        aligned_edge, counter_edge = long_edge_abs, short_edge_abs
    else:
        aligned_d, counter_d = short_d, long_d
        aligned_edge, counter_edge = short_edge_abs, long_edge_abs

    # Prefer direction-aligned; allow counter-trend only if edge is much better
    if aligned_d.allowed_to_trade and not counter_d.allowed_to_trade:
        chosen = aligned_d
    elif counter_d.allowed_to_trade and not aligned_d.allowed_to_trade:
        chosen = counter_d
        chosen.reasons.append("counter_trend_override")
    elif aligned_d.allowed_to_trade and counter_d.allowed_to_trade:
        if counter_edge >= aligned_edge * counter_trend_multiplier:
            chosen = counter_d
            chosen.reasons.append("counter_trend_override")
        else:
            chosen = aligned_d
    else:
        chosen = aligned_d
    chosen.flags["market_direction"] = market_direction_override or ("bullish" if market_bullish else "bearish")
    chosen.flags["direction_aligned"] = chosen.bias == aligned_d.bias
    return chosen


def build_decision(spot: float, pct: Percentiles, market_type: MarketType) -> Decision:
    """Legacy: single-direction decision. Prefer build_decision_with_market_strength."""
    return build_decision_with_market_strength(spot, pct, market_type)


def confirm_entry(
    bias: Bias,
    entry_price: float,
    one_min_candles: list[dict],
    five_min_candles: list[dict],
) -> bool:
    if bias == "flat" or len(one_min_candles) < 3 or len(five_min_candles) < 2:
        return False

    c1 = one_min_candles[-1]
    c2 = one_min_candles[-2]
    c3 = one_min_candles[-3]
    f = five_min_candles[-1]
    price = c1["close"]
    break_high = c1["close"] > c2["high"]
    break_low = c1["close"] < c2["low"]
    two_up = c2["close"] > c3["close"] and c1["close"] > c2["close"]
    two_down = c2["close"] < c3["close"] and c1["close"] < c2["close"]
    vwap = f.get("vwap", f["close"])
    bull_5m = f["close"] > f["open"] or f["close"] > vwap
    bear_5m = f["close"] < f["open"] or f["close"] < vwap

    tol = 0.003
    near_entry_long = price <= entry_price * (1 + tol)
    near_entry_short = price >= entry_price * (1 - tol)
    if bias == "long":
        momentum_ok = two_up or break_high or (price > c2["close"] and bull_5m)
        return near_entry_long and bull_5m and momentum_ok
    momentum_ok = two_down or break_low or (price < c2["close"] and bear_5m)
    return near_entry_short and bear_5m and momentum_ok


def tighten_stop(current_stop: float, bias: Bias, pct: Percentiles, tp1_hit: bool) -> float:
    if not tp1_hit:
        return current_stop
    if bias == "long":
        return max(current_stop, pct.p35)
    if bias == "short":
        return min(current_stop, pct.p65)
    return current_stop


def compute_position_size(
    account_equity: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    max_symbol_exposure: float,
) -> float:
    risk_amount = account_equity * risk_pct
    per_unit_risk = abs(entry_price - stop_price)
    if per_unit_risk <= 0:
        return 0.0
    raw_qty = risk_amount / per_unit_risk
    max_qty = (account_equity * max_symbol_exposure) / max(entry_price, 1e-9)
    return max(0.0, min(raw_qty, max_qty))


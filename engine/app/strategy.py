from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import Percentiles

Bias = Literal["long", "short", "flat"]
MarketType = Literal["crypto", "equity"]


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
    central_range: float
    range_: float
    flags: dict[str, bool]


def build_decision(spot: float, pct: Percentiles, market_type: MarketType, in_cooldown: bool) -> Decision:
    range_ = pct.p95 - pct.p05
    central_range = pct.p80 - pct.p20
    edge = (pct.p50 - spot) / spot
    uncertainty = range_ / spot
    reasons: list[str] = []
    flags = {
        "edge_filter_pass": abs(edge) >= 0.10 * uncertainty,
        "uncertainty_filter_pass": uncertainty <= (0.08 if market_type == "crypto" else 0.05),
        "cooldown_pass": not in_cooldown,
    }
    allowed = all(flags.values())
    if not flags["edge_filter_pass"]:
        reasons.append("edge_below_threshold")
    if not flags["uncertainty_filter_pass"]:
        reasons.append("uncertainty_too_high")
    if not flags["cooldown_pass"]:
        reasons.append("symbol_on_cooldown")

    if pct.p50 > spot:
        bias: Bias = "long"
        entry = max(pct.p35, spot - 0.20 * central_range)
        stop_base = (pct.p05 + pct.p20) / 2
        stop = stop_base - (0.08 * central_range)
        tp1 = (pct.p50 + pct.p65) / 2
        tp2 = pct.p65
    elif pct.p50 < spot:
        bias = "short"
        entry = min(pct.p65, spot + 0.20 * central_range)
        stop_base = (pct.p80 + pct.p95) / 2
        stop = stop_base + (0.08 * central_range)
        tp1 = (pct.p35 + pct.p50) / 2
        tp2 = pct.p35
    else:
        bias = "flat"
        entry = spot
        stop = spot
        tp1 = spot
        tp2 = spot
        reasons.append("no_direction")
        allowed = False
    return Decision(
        bias=bias,
        edge=edge,
        uncertainty=uncertainty,
        allowed_to_trade=allowed and bias != "flat",
        reasons=reasons,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        central_range=central_range,
        range_=range_,
        flags=flags,
    )


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


from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .strategy import build_decision, confirm_entry
from .synth_client import SynthClient


@dataclass
class BacktestResult:
    total_signals: int
    tradable_signals: int


def run_backtest(candles_1m: dict[str, list[dict[str, Any]]], candles_5m: dict[str, list[dict[str, Any]]], preds: dict[str, list[dict[str, Any]]]):
    total = 0
    tradable = 0
    for symbol, pred_list in preds.items():
        for pred in pred_list:
            total += 1
            p = SynthClient.parse_percentiles({"percentiles": pred["percentiles"]})
            spot = candles_1m[symbol][-1]["close"]
            decision = build_decision(spot, p, pred.get("market_type", "crypto"), in_cooldown=False)
            if decision.allowed_to_trade and confirm_entry(decision.bias, decision.entry, candles_1m[symbol][-3:], candles_5m[symbol][-2:]):
                tradable += 1
    return BacktestResult(total_signals=total, tradable_signals=tradable)


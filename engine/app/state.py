from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import SymbolConfig


@dataclass
class EngineState:
    symbols: list[SymbolConfig]
    trading_enabled: bool = True
    paper_trading: bool = True
    account_equity: float = 100_000.0
    crypto_risk_pct: float = 0.0075
    equity_risk_pct: float = 0.005
    max_symbol_exposure: float = 0.2
    max_portfolio_exposure: float = 0.7
    latest_signal: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_prediction: dict[str, dict[str, Any]] = field(default_factory=dict)
    latest_price: dict[str, float] = field(default_factory=dict)
    latest_market_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_market_data_error: str | None = None
    last_market_data_success_at: datetime | None = None
    exposure_by_symbol: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    last_updates: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    synth_api_calls: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def push_synth_call(self, api: str, params: dict[str, Any]) -> None:
        from datetime import datetime, timezone
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "api": api, "params": params}
        self.synth_api_calls.append(entry)
        self.push_update("synth_api_call", entry)

    def push_update(self, topic: str, payload: dict[str, Any]) -> None:
        self.last_updates.append(
            {
                "topic": topic,
                "payload": payload,
                "ts": datetime.utcnow().isoformat(),
            }
        )


from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4


MarketType = Literal["crypto", "equity"]


@dataclass(slots=True)
class BrokerOrder:
    order_id: str
    client_order_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    fill_price: float
    status: Literal["filled", "rejected", "open", "cancelled"]
    created_at: datetime
    reason: str = ""


TimeInForce = Literal["day", "gtc", "opg", "cls"]


class BrokerInterface(ABC):
    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        mid_price: float,
        market_type: MarketType,
        client_order_id: str | None = None,
        time_in_force: TimeInForce | str | None = None,
    ) -> BrokerOrder:
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_account(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def get_order_status(self, order_id: str) -> dict:
        raise NotImplementedError


class PaperBroker(BrokerInterface):
    def __init__(self, starting_equity: float, slippage_bps: float = 5.0) -> None:
        self.equity = starting_equity
        self.slippage_bps = slippage_bps
        self.orders: dict[str, BrokerOrder] = {}

    async def place_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: float,
        mid_price: float,
        market_type: MarketType,
        client_order_id: str | None = None,
        time_in_force: TimeInForce | str | None = None,
    ) -> BrokerOrder:
        coid = client_order_id or str(uuid4())
        if qty <= 0:
            order = BrokerOrder(
                order_id=str(uuid4()),
                client_order_id=coid,
                symbol=symbol,
                side=side,
                qty=qty,
                fill_price=mid_price,
                status="rejected",
                created_at=datetime.utcnow(),
                reason="qty_non_positive",
            )
            self.orders[order.order_id] = order
            return order
        slip = (self.slippage_bps / 10_000.0) * mid_price
        fill = mid_price + slip if side == "buy" else mid_price - slip
        order = BrokerOrder(
            order_id=str(uuid4()),
            client_order_id=coid,
            symbol=symbol,
            side=side,
            qty=qty,
            fill_price=fill,
            status="filled",
            created_at=datetime.utcnow(),
        )
        self.orders[order.order_id] = order
        return order

    async def cancel_order(self, order_id: str) -> None:
        if order_id in self.orders:
            o = self.orders[order_id]
            self.orders[order_id] = BrokerOrder(
                order_id=o.order_id,
                client_order_id=o.client_order_id,
                symbol=o.symbol,
                side=o.side,
                qty=o.qty,
                fill_price=o.fill_price,
                status="cancelled",
                created_at=o.created_at,
                reason="cancelled",
            )

    async def get_account(self) -> dict:
        return {"equity": self.equity}

    async def get_order_status(self, order_id: str) -> dict:
        o = self.orders.get(order_id)
        if not o:
            return {"status": "not_found"}
        return {"order_id": o.order_id, "status": o.status}


class CryptoBroker(PaperBroker):
    """Placeholder for live crypto broker integration behind shared interface."""


class EquityBroker(PaperBroker):
    """Placeholder for live equity broker integration behind shared interface."""


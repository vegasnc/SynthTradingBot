from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import Settings


class MongoStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.client = AsyncIOMotorClient(settings.mongo_uri)
        self.db: AsyncIOMotorDatabase = self.client[settings.engine_db_name]

    async def setup_indexes(self) -> None:
        await self.db.synth_predictions.create_index([("symbol", 1), ("timestamp", -1)])
        await self.db.candles_1m.create_index([("symbol", 1), ("ts", -1)], unique=True)
        await self.db.candles_5m.create_index([("symbol", 1), ("ts", -1)], unique=True)
        await self.db.signals.create_index([("symbol", 1), ("timestamp", -1)])
        await self.db.orders.create_index([("client_order_id", 1)], unique=True)
        await self.db.orders.create_index([("symbol", 1), ("created_at", -1)])
        await self.db.positions.create_index([("symbol", 1), ("status", 1), ("opened_at", -1)])
        await self.db.events.create_index([("ts", -1)])
        await self.db.synth_api_calls.create_index([("ts", -1)])

    async def insert_synth_call(self, api: str, params: dict[str, Any]) -> None:
        await self.db.synth_api_calls.insert_one({
            "ts": datetime.now(timezone.utc),
            "api": api,
            "params": params,
        })

    async def insert_event(self, level: str, event_type: str, message: str, extra: dict[str, Any] | None = None) -> None:
        await self.db.events.insert_one(
            {
                "ts": datetime.now(timezone.utc),
                "level": level,
                "type": event_type,
                "message": message,
                "extra": extra or {},
            }
        )


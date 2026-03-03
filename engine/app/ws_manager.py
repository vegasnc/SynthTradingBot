from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from starlette.websockets import WebSocket

from .state import EngineState


class WSManager:
    def __init__(self, state: EngineState) -> None:
        self.state = state
        self.clients: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for client in self.clients:
            try:
                await client.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(client)
        for ws in dead:
            self.disconnect(ws)

    async def stream_updates(self) -> AsyncGenerator[dict, None]:
        seen = 0
        while True:
            if len(self.state.last_updates) > seen:
                latest = list(self.state.last_updates)[seen:]
                seen = len(self.state.last_updates)
                for event in latest:
                    yield event
            await asyncio.sleep(0.5)


"""
core/live_feed.py

In-memory live-activity feed for the Stay Composed ops dashboard.

Every HTTP request that hits the API gets recorded here (by the middleware
in main.py) and broadcast to any connected dashboard browsers over
WebSocket. Nothing here touches route logic — it's purely observational.
"""

import asyncio
import json
from collections import deque
from typing import Deque, Dict, List

MAX_EVENTS = 200  # how much history a freshly-opened dashboard gets


class LiveFeedManager:
    def __init__(self) -> None:
        self._websockets: List = []
        self._lock = asyncio.Lock()
        self.events: Deque[dict] = deque(maxlen=MAX_EVENTS)
        self.stats: Dict[str, int] = {
            "total": 0,
            "items": 0,
            "claims": 0,
            "chat": 0,
            "alert": 0,
            "other": 0,
        }

    async def connect(self, websocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._websockets.append(websocket)
        await websocket.send_text(json.dumps({
            "type": "init",
            "events": list(self.events),
            "stats": self.stats,
            "viewers": len(self._websockets),
        }))
        await self._broadcast_viewers()

    async def disconnect(self, websocket) -> None:
        async with self._lock:
            if websocket in self._websockets:
                self._websockets.remove(websocket)
        await self._broadcast_viewers()

    async def record(self, event: dict) -> None:
        self.events.append(event)
        self.stats["total"] += 1
        category = event.get("category", "other")
        self.stats[category] = self.stats.get(category, 0) + 1
        await self._send_all({"type": "event", "event": event, "stats": self.stats})

    async def _broadcast_viewers(self) -> None:
        await self._send_all({"type": "viewers", "viewers": len(self._websockets)})

    async def _send_all(self, payload: dict) -> None:
        if not self._websockets:
            return
        data = json.dumps(payload)
        dead = []
        for ws in list(self._websockets):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._websockets:
                        self._websockets.remove(ws)


live_feed = LiveFeedManager()

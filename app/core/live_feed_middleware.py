"""
core/live_feed_middleware.py

ASGI middleware that times every HTTP request, classifies it by area
(items / claims / chat / blood-alert / other), pulls a couple of
display-friendly fields out of the JSON response when it can, and hands
the result to live_feed.record() for broadcast to the ops dashboard.

Only wraps http requests — the WebSocket chat endpoint (scope type
"websocket") is untouched by Starlette's BaseHTTPMiddleware, so this adds
zero overhead or interference there.
"""

import json
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.live_feed import live_feed


def _category_for(path: str) -> tuple[str, str]:
    """Returns (category_key, category_label) for the filter chips."""
    if path.startswith("/items"):
        return "items", "ITEMS"
    if path.startswith("/claims"):
        return "claims", "CLAIMS"
    if path.startswith("/chat"):
        return "chat", "CHAT"
    if path.startswith("/blood-alert"):
        return "alert", "BLOOD ALERT"
    return "other", "SYSTEM"


def _extract_fields(path: str, body: dict) -> dict:
    """Best-effort pull of a couple of domain fields for the ticket's detail row."""
    fields: dict = {}
    if not isinstance(body, dict):
        return fields
    if path.startswith("/items"):
        if "type" in body:
            fields["item_type"] = body.get("type")
        if "title" in body:
            fields["title"] = body.get("title")
        if "status" in body:
            fields["item_status"] = body.get("status")
        if "candidateMatches" in body:
            fields["matches"] = len(body.get("candidateMatches") or []) + len(body.get("founderMatches") or [])
    elif path.startswith("/claims"):
        if "verified" in body:
            fields["verified"] = body.get("verified")
        if "matchedFields" in body:
            fields["matched"] = f'{body.get("matchedFields")}/{body.get("totalFields")}'
    elif path.startswith("/chat"):
        if "confidence" in body:
            fields["confidence"] = body.get("confidence")
        if "status" in body:
            fields["thread_status"] = body.get("status")
    elif path.startswith("/blood-alert"):
        if "recipientsNotified" in body:
            fields["recipients"] = body.get("recipientsNotified")
        if "bloodType" in body:
            fields["blood_type"] = body.get("bloodType")
    return fields


class LiveFeedMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        path = request.url.path
        category, category_label = _category_for(path)

        # Buffer the response body so we can peek at it, then hand an
        # identical stream back downstream — response.body_iterator only
        # yields once, so we must reconstruct it.
        body_bytes = b""
        try:
            async for chunk in response.body_iterator:
                body_bytes += chunk
        except Exception:
            body_bytes = b""

        parsed = None
        if body_bytes:
            try:
                parsed = json.loads(body_bytes)
            except Exception:
                parsed = None

        event = {
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "time": datetime.now(timezone.utc).isoformat(),
            "client": request.client.host if request.client else "",
            "category": category,
            "category_label": category_label,
            **_extract_fields(path, parsed),
        }

        try:
            await live_feed.record(event)
        except Exception:
            pass  # never let dashboard telemetry break a real request

        from starlette.responses import Response as StarletteResponse
        return StarletteResponse(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

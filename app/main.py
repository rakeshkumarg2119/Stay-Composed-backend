import pathlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import get_settings
from app.core.live_feed import live_feed
from app.core.live_feed_middleware import LiveFeedMiddleware
from app.database import ensure_indexes
from app.routers import blood_alert, chat, claims, devices, items, notifications

settings = get_settings()

app = FastAPI(title="Stay Composed API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Records every HTTP request for the /ops live dashboard. Websocket scope
# (the chat socket) passes straight through, untouched.
app.add_middleware(LiveFeedMiddleware)

app.include_router(items.router)
app.include_router(claims.router)
app.include_router(blood_alert.router)
app.include_router(chat.router)
app.include_router(devices.router)
app.include_router(notifications.router)


@app.websocket("/ws/live")
async def ops_live_socket(websocket: WebSocket):
    await live_feed.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # dashboard doesn't send anything; just keep-alive
    except WebSocketDisconnect:
        await live_feed.disconnect(websocket)


@app.get("/ops")
async def ops_dashboard():
    dashboard_path = pathlib.Path(__file__).parent / "static" / "dashboard.html"
    return FileResponse(dashboard_path)


@app.get("/")
async def root():
    return {"status": "ok", "service": "stay-composed-backend"}


@app.on_event("startup")
async def on_startup():
    await ensure_indexes()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "stay-composed-backend"}


@app.get("/api/test")
async def api_test():
    return {"status": "ok"}
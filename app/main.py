from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import ensure_indexes
from app.routers import blood_alert, chat, claims, items

settings = get_settings()

app = FastAPI(title="Stay Composed API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(items.router)
app.include_router(claims.router)
app.include_router(blood_alert.router)
app.include_router(chat.router)


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

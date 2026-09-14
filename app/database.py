from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def get_db() -> AsyncIOMotorDatabase:
    global _client, _db
    if _db is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongodb_uri)
        _db = _client[settings.db_name]
    return _db


def items_collection():
    return get_db()["items"]


def claims_collection():
    return get_db()["claim_attempts"]


def staff_collection():
    return get_db()["staff_directory"]


def blood_alerts_collection():
    return get_db()["blood_alerts"]


def chat_threads_collection():
    return get_db()["chat_threads"]


def chat_messages_collection():
    return get_db()["chat_messages"]


async def ensure_indexes() -> None:
    db = get_db()
    await db["items"].create_index([("type", 1), ("reporterEmail", 1)])
    await db["items"].create_index([("status", 1)])
    await db["claim_attempts"].create_index([("claimantEmail", 1), ("foundItemId", 1)])
    await db["staff_directory"].create_index("email", unique=True)
    await db["chat_threads"].create_index([("complaintId", 1), ("foundItemId", 1)], unique=True)
    await db["chat_messages"].create_index([("threadId", 1), ("sentAt", 1)])
    await db["chat_messages"].create_index([("senderEmail", 1), ("sentAt", 1)])

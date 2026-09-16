from fastapi import APIRouter

from app.database import device_tokens_collection
from app.models import DeviceTokenRegister

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register")
async def register_device_token(payload: DeviceTokenRegister):
    """
    Upsert by token, not by email — a token uniquely identifies one
    installed app instance, and the same email can have several (phone +
    tablet, or a reinstall that got a fresh token from FCM).

    Call this on login AND on FirebaseMessaging.instance.onTokenRefresh —
    FCM rotates tokens periodically and the old one silently stops
    delivering if never updated here.
    """
    await device_tokens_collection().update_one(
        {"token": payload.token},
        {"$set": {"email": payload.email, "platform": payload.platform}},
        upsert=True,
    )
    return {"status": "ok"}


@router.delete("/register")
async def unregister_device_token(token: str):
    """Call on logout so a signed-out device stops receiving that user's pushes."""
    await device_tokens_collection().delete_one({"token": token})
    return {"status": "ok"}

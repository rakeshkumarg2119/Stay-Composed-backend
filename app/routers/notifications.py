from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from app.database import notifications_collection
from app.models import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(email: str = Query(...)):
    """
    This route did not exist before — notifications_provider.dart's
    fetch() was calling GET /notifications with no matching backend route
    at all, which is the actual reason the Notifications screen has always
    come back empty. Newest first, capped at 100 so the screen can't be
    handed an unbounded feed from a long-lived account.
    """
    coll = notifications_collection()
    cursor = coll.find({"email": email}).sort("createdAt", -1).limit(100)
    docs = [d async for d in cursor]
    return [
        NotificationOut(
            id=str(d["_id"]),
            type=d["type"],
            title=d["title"],
            body=d["body"],
            createdAt=d["createdAt"],
            isRead=d.get("isRead", False),
            relatedId=d.get("relatedId"),
        )
        for d in docs
    ]


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str):
    try:
        oid = ObjectId(notification_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid notification id.")

    result = await notifications_collection().update_one({"_id": oid}, {"$set": {"isRead": True}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"success": True}

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.database import blood_alerts_collection, staff_collection
from app.models import BloodAlertRequest, BloodAlertResult
from app.services.email_service import send_blood_alert

router = APIRouter(prefix="/blood-alert", tags=["blood-alert"])


@router.get("/mine")
async def my_blood_alerts(email: str = Query(...)):
    docs = [
        {
            "id": str(d["_id"]),
            "studentName": d.get("studentName"),
            "bloodType": d.get("bloodType"),
            "phoneNumber": d.get("phoneNumber"),
            "senderEmail": d.get("senderEmail"),
            "recipientsNotified": d.get("recipientsNotified", 0),
            "createdAt": d.get("createdAt").isoformat() if d.get("createdAt") else None,
        }
        async for d in blood_alerts_collection().find({"senderEmail": email}).sort("createdAt", -1)
    ]
    return docs


@router.post("", response_model=BloodAlertResult)
async def create_blood_alert(payload: BloodAlertRequest):
    # No department field / filter: every request is broadcast to the ENTIRE
    # staff directory across all departments, so rare blood types reach the
    # widest possible pool of potential donors.
    staff_docs = [d async for d in staff_collection().find({}, {"email": 1})]
    recipients = [d["email"] for d in staff_docs if d.get("email")]

    sent_count = await send_blood_alert(
        recipients=recipients,
        student_name=payload.studentName,
        blood_type=payload.bloodType,
        phone_number=payload.phoneNumber,
        sender_email=payload.senderEmail,
    )

    await blood_alerts_collection().insert_one(
        {
            "studentName": payload.studentName,
            "bloodType": payload.bloodType,
            "phoneNumber": payload.phoneNumber,
            "senderEmail": payload.senderEmail,
            "recipientsNotified": sent_count,
            "scope": "campus-wide-all-departments",
            "createdAt": datetime.now(timezone.utc),
        }
    )

    return BloodAlertResult(
        success=True,
        recipientsNotified=sent_count,
        message=f"Broadcast sent campus-wide to {sent_count} staff member(s) across all departments.",
    )


@router.get("/staff")
async def list_staff_recipients():
    staff_docs = [
        {"email": d["email"], "department": d.get("department", "Staff / Faculty")}
        async for d in staff_collection().find({}, {"email": 1, "department": 1})
        if d.get("email")
    ]
    email_list = [d["email"] for d in staff_docs]
    return {"staff": email_list, "details": staff_docs}


@router.post("/staff")
async def add_staff_recipient(payload: dict):
    email = payload.get("email", "").strip().lower()
    department = payload.get("department", "Judge / Guest Faculty").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email address is required.")

    await staff_collection().update_one(
        {"email": email},
        {"$set": {"email": email, "department": department, "addedAt": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"status": "ok", "email": email}


@router.delete("/staff/{email}")
async def remove_staff_recipient(email: str):
    clean_email = email.strip().lower()
    result = await staff_collection().delete_one({"email": clean_email})
    return {"status": "ok", "deletedCount": result.deleted_count}

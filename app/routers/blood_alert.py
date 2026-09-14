from datetime import datetime, timezone

from fastapi import APIRouter

from app.database import blood_alerts_collection, staff_collection
from app.models import BloodAlertRequest, BloodAlertResult
from app.services.email_service import send_blood_alert

router = APIRouter(prefix="/blood-alert", tags=["blood-alert"])


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

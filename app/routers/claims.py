from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.database import claims_collection, items_collection
from app.models import ClaimRequest, ClaimResult
from app.security import verify_secret

router = APIRouter(prefix="/claims", tags=["claims"])


@router.post("", response_model=ClaimResult)
async def submit_claim(payload: ClaimRequest):
    settings = get_settings()
    now = datetime.now(timezone.utc)

    # --- Rate limiting / cooldown: prevent brute-forcing secret details ---
    attempts_coll = claims_collection()
    window_start = now - timedelta(minutes=settings.claim_cooldown_minutes)
    recent_attempts = await attempts_coll.count_documents(
        {
            "claimantEmail": payload.claimantEmail,
            "foundItemId": payload.foundItemId,
            "attemptedAt": {"$gte": window_start},
        }
    )
    if recent_attempts >= settings.claim_max_attempts:
        cooldown_until = now + timedelta(minutes=settings.claim_cooldown_minutes)
        return ClaimResult(
            verified=False,
            matchedFields=0,
            totalFields=0,
            message="Too many claim attempts. Please wait before trying again.",
            cooldownUntil=cooldown_until,
        )

    complaint = await items_collection().find_one({"_id": payload.complaintId, "type": "lost"})
    if not complaint or complaint.get("reporterEmail") != payload.claimantEmail:
        raise HTTPException(status_code=403, detail="You can only claim matches against your own complaint.")

    found = await items_collection().find_one({"_id": payload.foundItemId, "type": "found"})
    if not found:
        raise HTTPException(status_code=404, detail="Found item not found.")
    if found.get("status") not in ("open", "matched"):
        raise HTTPException(status_code=409, detail="This item is no longer available to claim.")

    stored_hashes: list[str] = found.get("secretAnswerHashes", [])
    total_fields = len(stored_hashes)
    if total_fields == 0:
        raise HTTPException(status_code=400, detail="This found item has no verification challenge configured.")

    matched = 0
    for i, stored_hash in enumerate(stored_hashes):
        answer = payload.answers[i] if i < len(payload.answers) else ""
        if answer and verify_secret(answer, stored_hash):
            matched += 1

    # Majority match required — a single lucky guess can't fake ownership
    verified = matched > total_fields / 2

    await attempts_coll.insert_one(
        {
            "claimantEmail": payload.claimantEmail,
            "foundItemId": payload.foundItemId,
            "complaintId": payload.complaintId,
            "attemptedAt": now,
            "verified": verified,
            "matchedFields": matched,
            "totalFields": total_fields,
        }
    )

    if verified:
        # Chat closes permanently once verification succeeds — mark both sides resolved
        await items_collection().update_one({"_id": found["_id"]}, {"$set": {"status": "resolved"}})
        await items_collection().update_one({"_id": complaint["_id"]}, {"$set": {"status": "resolved"}})
        message = "Ownership verified! Majority of the secret details matched. This claim is now closed."
    else:
        message = f"Verification failed — only {matched} of {total_fields} secret detail(s) matched. Try again or contact admin support."

    return ClaimResult(verified=verified, matchedFields=matched, totalFields=total_fields, message=message)

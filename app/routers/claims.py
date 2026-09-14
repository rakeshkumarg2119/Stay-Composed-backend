from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.database import chat_threads_collection, claims_collection, items_collection
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

    # If this pair hit the high-confidence chat gate, a thread exists — in that
    # case the founder must have explicitly started verification (locking the
    # chat) before a claim can be submitted. Pairs that never reached chat
    # (no thread) keep the original direct-claim path.
    thread_id = f"{payload.complaintId}:{payload.foundItemId}"
    thread = await chat_threads_collection().find_one({"_id": thread_id})
    if thread and thread["status"] not in ("verifying", "resolved"):
        raise HTTPException(
            status_code=409,
            detail="The founder hasn't started verification yet — wait for them to begin it from the chat.",
        )

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
        # Mark both items as verified (removes found item from any other candidate recommendations)
        # Keep chat thread in 'verified' status so parties can coordinate meeting for physical handover
        await items_collection().update_one({"_id": found["_id"]}, {"$set": {"status": "verified"}})
        await items_collection().update_one({"_id": complaint["_id"]}, {"$set": {"status": "verified"}})
        if thread:
            await chat_threads_collection().update_one({"_id": thread_id}, {"$set": {"status": "verified"}})
            from app.routers.chat import manager
            await manager.broadcast(thread_id, {"type": "verification_completed", "verified": True})
        message = "Ownership verified! Majority of the secret details matched. You can now coordinate meeting for handover in the chat."
    else:
        message = f"Verification failed — only {matched} of {total_fields} secret detail(s) matched. Try again or contact admin support."

    return ClaimResult(verified=verified, matchedFields=matched, totalFields=total_fields, message=message)

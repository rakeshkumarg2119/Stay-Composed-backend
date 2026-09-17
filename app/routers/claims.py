from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.database import chat_threads_collection, claims_collection, items_collection
from app.models import ClaimRequest, ClaimResult
from app.security import verify_secret
from app.services.text_similarity_service import (
    MODEL_VERSION as ANSWER_EMBEDDING_MODEL_VERSION,
    cosine_similarity,
    embed_answer_text,
)

router = APIRouter(prefix="/claims", tags=["claims"])

# Calibrated for all-MiniLM-L6-v2 (see text_similarity_service.py), NOT the
# old CLIP model's 0.68 — that threshold was measured against a text tower
# that isn't discriminative for unrelated short phrases in the first place,
# so it doesn't transfer. Unrelated pairs on MiniLM typically land ~0.0-0.3;
# genuine paraphrases of a short factual answer land ~0.55+. Still worth
# validating with your own known-wrong-answer pairs before trusting this in
# production; tune from there rather than treating 0.60 as final.
SEMANTIC_MATCH_THRESHOLD = 0.60


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
    stored_embeddings: list[list[float]] = found.get("secretAnswerEmbeddings", [])
    # Only trust the stored embeddings for Tier 2 if they were built with the
    # model we're comparing against right now. The plaintext answer is never
    # stored (only the bcrypt hash below), so there's no way to re-embed an
    # older record after a model change — items registered before this field
    # existed, or with an older model tag, fall back to Tier 1 (exact match)
    # only, rather than silently comparing vectors from two different spaces.
    embeddings_usable = found.get("secretAnswerEmbeddingModel") == ANSWER_EMBEDDING_MODEL_VERSION
    total_fields = len(stored_hashes)
    if total_fields == 0:
        raise HTTPException(status_code=400, detail="This found item has no verification challenge configured.")

    matched = 0
    exact_matches = 0
    semantic_matches = 0

    for i, stored_hash in enumerate(stored_hashes):
        answer = payload.answers[i] if i < len(payload.answers) else ""
        if not answer or not answer.strip():
            continue

        clean_ans = answer.strip()
        # Tier 1: Exact or case/whitespace-normalized bcrypt match
        if verify_secret(clean_ans, stored_hash):
            matched += 1
            exact_matches += 1
            continue

        # Tier 2: AI Semantic similarity check using a dedicated sentence-
        # embedding model (see text_similarity_service.py — deliberately NOT
        # CLIP, whose text tower doesn't reliably separate unrelated short
        # phrases from genuine paraphrases). Protects claimants from being
        # locked out over phrasing/spelling variations, without letting
        # wrong answers slide through on an uncalibrated threshold.
        if embeddings_usable and i < len(stored_embeddings) and stored_embeddings[i]:
            try:
                ans_emb = embed_answer_text(clean_ans)
                stored_vec = np.array(stored_embeddings[i])
                sim = cosine_similarity(ans_emb, stored_vec)
                if sim is not None and sim >= SEMANTIC_MATCH_THRESHOLD:
                    matched += 1
                    semantic_matches += 1
            except Exception:
                pass

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
            "exactMatches": exact_matches,
            "semanticMatches": semantic_matches,
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
            await manager.broadcast(thread_id, {"event": "phase_changed", "status": "verified"})
            await manager.broadcast(thread_id, {"type": "verification_completed", "verified": True})
        
        detail_note = " (including AI semantic verification)" if semantic_matches > 0 else ""
        message = f"Ownership verified! {matched} of {total_fields} secret detail(s) matched{detail_note}. You can now coordinate meeting for handover in the chat."
    else:
        message = f"Verification failed — only {matched} of {total_fields} secret detail(s) matched. Try again or contact admin support."

    return ClaimResult(verified=verified, matchedFields=matched, totalFields=total_fields, message=message)
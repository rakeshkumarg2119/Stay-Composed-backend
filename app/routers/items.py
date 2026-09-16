import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.database import items_collection
from app.models import CandidateMatch, ItemCreate, ItemOut, MineResponse
from app.security import hash_secret, mask_display_name
from app.services.clip_service import embed_image_url, embed_text
from app.services.email_service import send_match_found_email
from app.services.matching import score_pair
from app.services.push_service import send_push_to_email
from app.utils.locations import format_location, validate_location

logger = logging.getLogger("items")

router = APIRouter(prefix="/items", tags=["items"])


def _text_blob(title: str, description: str) -> str:
    return f"{title}. {description}".strip()


def _to_out(doc: dict, include_secrets: bool = False) -> ItemOut:
    return ItemOut(
        id=doc["_id"],
        type=doc["type"],
        title=doc["title"],
        category=doc.get("category", "General"),
        location=doc.get("location"),
        date=doc.get("date"),
        description=doc["description"],
        imageUrl=doc.get("imageUrl"),
        secretFeatures=doc.get("secretFeatures") if include_secrets else None,
        challengeQuestions=doc.get("challengeQuestions"),
        reportedBy=doc["reportedBy"],
        status=doc.get("status", "open"),
        createdAt=doc["createdAt"],
    )


@router.post("", response_model=ItemOut)
async def create_item(payload: ItemCreate):
    loc_error = validate_location(payload.type, payload.location, payload.locationDetail)
    if loc_error:
        raise HTTPException(status_code=400, detail=loc_error)

    if payload.type == "found":
        if not payload.imageUrl or not payload.imageUrl.strip():
            raise HTTPException(status_code=400, detail="Image upload is compulsory for found items.")
        if not payload.challengeQuestions or not payload.secretAnswers:
            raise HTTPException(
                status_code=400,
                detail="At least one challenge question with its secret answer is required so ownership can be verified later.",
            )
        if len(payload.challengeQuestions) != len(payload.secretAnswers):
            raise HTTPException(status_code=400, detail="Every challenge question needs exactly one matching secret answer.")

    if payload.type == "lost":
        if not payload.secretFeatures or not any(f.strip() for f in payload.secretFeatures):
            raise HTTPException(
                status_code=400,
                detail="Secret verification feature(s) are required to prove true ownership later.",
            )

    item_id = f"item-{uuid.uuid4().hex[:12]}"
    resolved_location = format_location(payload.location, payload.locationDetail)
    text_blob = _text_blob(payload.title, payload.description)

    doc = {
        "_id": item_id,
        "type": payload.type,
        "title": payload.title.strip(),
        "category": payload.category or "General",
        "location": resolved_location,
        "date": payload.date,
        "description": payload.description.strip(),
        "imageUrl": payload.imageUrl,
        "reportedBy": mask_display_name(payload.reporterName, payload.reporterEmail),
        "reporterEmail": payload.reporterEmail,
        "status": "open",
        "createdAt": datetime.now(timezone.utc),
        # CLIP embeddings, stored as plain lists so Mongo can serialize them
        "textEmbedding": _safe_list(embed_text(text_blob)),
        "imageEmbedding": _safe_list(embed_image_url(payload.imageUrl)),
    }

    if payload.type == "lost":
        clean_features = [f.strip() for f in (payload.secretFeatures or []) if f.strip()]
        doc["secretFeatures"] = clean_features
        doc["secretFeatureHashes"] = [hash_secret(f) for f in clean_features]
    else:
        clean_questions = [q.strip() for q in (payload.challengeQuestions or []) if q.strip()]
        clean_answers = [a.strip() for a in (payload.secretAnswers or []) if a.strip()]
        doc["challengeQuestions"] = clean_questions
        doc["secretAnswerHashes"] = [hash_secret(a) for a in clean_answers]
        doc["secretAnswerEmbeddings"] = [_safe_list(embed_text(a)) for a in clean_answers]

    await items_collection().insert_one(doc)
    await _notify_new_matches(doc)
    return _to_out(doc, include_secrets=True)


def _safe_list(vec):
    return vec.tolist() if vec is not None else None


def _scored(doc: dict) -> dict:
    import numpy as np

    return {
        "category": doc.get("category"),
        "location": doc.get("location"),
        "_textEmbedding": np.array(doc["textEmbedding"]) if doc.get("textEmbedding") else None,
        "_imageEmbedding": np.array(doc["imageEmbedding"]) if doc.get("imageEmbedding") else None,
    }


async def _notify_new_matches(new_doc: dict) -> None:
    """
    Runs once, right after a new item is inserted — scores it against every
    open item of the opposite type and emails both parties on any pair
    crossing chat_min_confidence (same threshold that unlocks chat).

    Deliberately email-only for now: there's no FCM push infra in this
    backend yet (no device-token storage, no firebase-admin dependency
    confirmed, no registration endpoint) — that's a separate build, not a
    one-line hook like this one.

    Failures here are logged, never raised — a broken notification must
    never fail the actual item report.
    """
    settings = get_settings()
    opposite_type = "lost" if new_doc["type"] == "found" else "found"

    try:
        candidates = [d async for d in items_collection().find({"type": opposite_type, "status": "open"})]
    except Exception:
        logger.exception("Could not load candidates for match notification on %s", new_doc["_id"])
        return

    new_scored = _scored(new_doc)
    for other in candidates:
        if other.get("status") in ("handed_over", "resolved"):
            continue
        try:
            confidence = score_pair(
                new_scored if new_doc["type"] == "lost" else _scored(other),
                _scored(other) if new_doc["type"] == "lost" else new_scored,
            )
        except Exception:
            logger.exception("Scoring failed for %s vs %s", new_doc["_id"], other["_id"])
            continue

        if confidence < settings.chat_min_confidence:
            continue

        lost_doc, found_doc = (new_doc, other) if new_doc["type"] == "lost" else (other, new_doc)

        try:
            await send_match_found_email(
                lost_doc["reporterEmail"],
                is_lost_reporter=True,
                other_item_title=found_doc["title"],
                confidence=confidence,
            )
            await send_match_found_email(
                found_doc["reporterEmail"],
                is_lost_reporter=False,
                other_item_title=lost_doc["title"],
                confidence=confidence,
            )
        except Exception:
            logger.exception("Failed sending match-found emails for %s <-> %s", lost_doc["_id"], found_doc["_id"])

        try:
            await send_push_to_email(
                lost_doc["reporterEmail"],
                title="Possible match found",
                body=f"A found item may match your lost report: {found_doc['title']}",
                data={
                    "type": "match_found",
                    "relatedId": lost_doc["_id"],
                    "complaintId": lost_doc["_id"],
                    "foundItemId": found_doc["_id"],
                    "confidence": str(confidence),
                },
            )
            await send_push_to_email(
                found_doc["reporterEmail"],
                title="Possible match found",
                body=f"Your found item may match a lost report: {lost_doc['title']}",
                data={
                    "type": "match_found",
                    "relatedId": found_doc["_id"],
                    "complaintId": lost_doc["_id"],
                    "foundItemId": found_doc["_id"],
                    "confidence": str(confidence),
                },
            )
        except Exception:
            logger.exception("Failed sending match-found push for %s <-> %s", lost_doc["_id"], found_doc["_id"])


@router.get("/mine", response_model=MineResponse)
async def my_items(email: str = Query(...)):
    settings = get_settings()
    coll = items_collection()

    my_complaints_docs = [d async for d in coll.find({"type": "lost", "reporterEmail": email})]
    my_found_docs = [d async for d in coll.find({"type": "found", "reporterEmail": email})]

    candidate_matches: list[CandidateMatch] = []
    if my_complaints_docs:
        all_found_docs = [d async for d in coll.find({"type": "found", "status": "open"})]
        import numpy as np

        for lost in my_complaints_docs:
            if lost.get("status") in ("handed_over", "resolved"):
                continue
            lost_scored = {
                "category": lost.get("category"),
                "location": lost.get("location"),
                "_textEmbedding": np.array(lost["textEmbedding"]) if lost.get("textEmbedding") else None,
                "_imageEmbedding": np.array(lost["imageEmbedding"]) if lost.get("imageEmbedding") else None,
            }
            for found in all_found_docs:
                found_scored = {
                    "category": found.get("category"),
                    "location": found.get("location"),
                    "_textEmbedding": np.array(found["textEmbedding"]) if found.get("textEmbedding") else None,
                    "_imageEmbedding": np.array(found["imageEmbedding"]) if found.get("imageEmbedding") else None,
                }
                confidence = score_pair(lost_scored, found_scored)
                if confidence >= settings.match_min_confidence:
                    candidate_matches.append(
                        CandidateMatch(candidate=_to_out(found, include_secrets=False), forComplaintId=lost["_id"], confidence=confidence)
                    )

    candidate_matches.sort(key=lambda m: m.confidence, reverse=True)

    return MineResponse(
        myComplaints=[_to_out(d, include_secrets=True) for d in my_complaints_docs],
        myFoundItems=[_to_out(d, include_secrets=True) for d in my_found_docs],
        candidateMatches=candidate_matches,
        chatConfidenceThreshold=settings.chat_min_confidence,
    )


DEFAULT_DEMO_EMAILS = "24suca17@tcarts.in,24suca11@tcarts.in,24suca111@tcarts.in"


@router.delete("/demo-reset")
async def demo_reset(emails: str = Query(DEFAULT_DEMO_EMAILS)):
    """
    Cleans up all demo items and their associated chat threads / messages
    for the hackathon presentation test accounts so the database is
    clean and ready for another presentation run.
    """
    from app.database import chat_messages_collection, chat_threads_collection, claims_collection

    raw_emails = emails if isinstance(emails, str) else DEFAULT_DEMO_EMAILS
    email_list = [e.strip().lower() for e in raw_emails.split(",") if e.strip()]
    if not email_list:
        return {"status": "ok", "deletedItems": 0, "deletedThreads": 0}

    # 1. Collect all matching item IDs to also clean up any cross-referenced threads
    item_docs = [d async for d in items_collection().find({"reporterEmail": {"$in": email_list}}, {"_id": 1})]
    item_ids = [d["_id"] for d in item_docs]

    # 2. Delete the items
    item_res = await items_collection().delete_many({"reporterEmail": {"$in": email_list}})

    # 3. Delete threads and their messages
    thread_docs = [
        d async for d in chat_threads_collection().find(
            {"$or": [{"claimantEmail": {"$in": email_list}}, {"founderEmail": {"$in": email_list}}, {"foundItemId": {"$in": item_ids}}, {"complaintId": {"$in": item_ids}}]},
            {"_id": 1}
        )
    ]
    thread_ids = [d["_id"] for d in thread_docs]

    thread_res = await chat_threads_collection().delete_many({"_id": {"$in": thread_ids}})
    if thread_ids:
        await chat_messages_collection().delete_many({"threadId": {"$in": thread_ids}})

    # 4. Clean up claim attempts for these items and accounts
    await claims_collection().delete_many({
        "$or": [
            {"claimantEmail": {"$in": email_list}},
            {"foundItemId": {"$in": item_ids}},
            {"complaintId": {"$in": item_ids}},
        ]
    })

    return {
        "status": "ok",
        "deletedItems": item_res.deleted_count,
        "deletedThreads": thread_res.deleted_count,
    }
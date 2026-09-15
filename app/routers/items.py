import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.database import items_collection
from app.models import CandidateMatch, ItemCreate, ItemOut, MineResponse
from app.security import hash_secret, mask_display_name
from app.services.clip_service import embed_image_url, embed_text
from app.services.matching import score_pair
from app.utils.locations import format_location, validate_location

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
    return _to_out(doc, include_secrets=True)


def _safe_list(vec):
    return vec.tolist() if vec is not None else None


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
    )

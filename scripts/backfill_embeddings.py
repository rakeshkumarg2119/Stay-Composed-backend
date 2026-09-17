"""
One-off backfill: re-embeds any existing item whose textEmbedding or
imageEmbedding is missing/null in Mongo.

This is only needed for items created BEFORE the embed_image_url retry fix
landed — those got permanently stuck with imageEmbedding: null the moment
a single Cloudinary CDN read failed, since embed_image_url used to give up
on the first failure with no retry.

Run from the backend project root (same place you'd run uvicorn from):

    python -m scripts.backfill_embeddings

or, if this file lives elsewhere, adjust the imports/path accordingly so
`app.*` resolves.
"""

import asyncio
import logging

from app.database import items_collection
from app.services.clip_service import embed_image_url, embed_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_embeddings")


def _text_blob(doc: dict) -> str:
    return f"{doc.get('title', '')}. {doc.get('description', '')}".strip()


def _safe_list(vec):
    return vec.tolist() if vec is not None else None


async def main():
    coll = items_collection()

    # Anything missing either embedding, or where the field doesn't exist
    # at all (older docs predating the embedding fields entirely).
    query = {
        "$or": [
            {"textEmbedding": None},
            {"textEmbedding": {"$exists": False}},
            {"imageEmbedding": None},
            {"imageEmbedding": {"$exists": False}},
        ]
    }

    docs = [d async for d in coll.find(query)]
    logger.info("Found %d item(s) with missing embeddings", len(docs))

    fixed = 0
    still_broken = []

    for doc in docs:
        item_id = doc["_id"]
        updates = {}

        if not doc.get("textEmbedding"):
            text_vec = embed_text(_text_blob(doc))
            if text_vec is not None:
                updates["textEmbedding"] = _safe_list(text_vec)
            else:
                logger.warning("%s: still no textEmbedding (empty title/description?)", item_id)

        if not doc.get("imageEmbedding") and doc.get("imageUrl"):
            img_vec = embed_image_url(doc["imageUrl"])  # now retries internally
            if img_vec is not None:
                updates["imageEmbedding"] = _safe_list(img_vec)
            else:
                logger.warning("%s: still no imageEmbedding after retries — imageUrl may be dead: %s", item_id, doc["imageUrl"])
                still_broken.append(item_id)

        if updates:
            await coll.update_one({"_id": item_id}, {"$set": updates})
            fixed += 1
            logger.info("%s: updated %s", item_id, list(updates.keys()))

    logger.info("Done. Updated %d item(s). %d still missing an image embedding: %s", fixed, len(still_broken), still_broken)


if __name__ == "__main__":
    asyncio.run(main())

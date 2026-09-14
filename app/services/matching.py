"""
Ranks a lost-item complaint against found-item candidates.

Score is a weighted blend of:
  - CLIP text embedding similarity (title + description)        -> up to 45 pts
  - CLIP image embedding similarity (when both sides have a photo) -> up to 35 pts
  - Category match                                               -> 12 pts
  - Location match                                                -> 8 pts

This is intentionally a *ranked list with confidence scores*, not a single
yes/no — final ownership is still only proven through the hashed
secret-detail challenge (see claims.py), never by AI score alone.
"""

from app.services.clip_service import cosine_similarity, embed_image_url, embed_text


def _text_blob(item: dict) -> str:
    return f"{item.get('title', '')}. {item.get('description', '')}".strip()


def score_pair(lost: dict, found: dict) -> int:
    score = 0.0

    # --- CLIP text similarity ---
    lost_text_vec = lost.get("_textEmbedding")
    found_text_vec = found.get("_textEmbedding")
    text_sim = cosine_similarity(lost_text_vec, found_text_vec)
    if text_sim is not None:
        # cosine is roughly in [-1, 1]; clamp+scale to [0, 45]
        score += max(0.0, text_sim) * 45

    # --- CLIP image similarity (only if both have photos) ---
    lost_img_vec = lost.get("_imageEmbedding")
    found_img_vec = found.get("_imageEmbedding")
    img_sim = cosine_similarity(lost_img_vec, found_img_vec)
    if img_sim is not None:
        score += max(0.0, img_sim) * 35

    # --- Category bonus ---
    if lost.get("category") and found.get("category"):
        if lost["category"].strip().lower() == found["category"].strip().lower():
            score += 12

    # --- Location bonus ---
    if lost.get("location") and found.get("location"):
        if lost["location"].strip().lower() == found["location"].strip().lower():
            score += 8

    return int(round(min(98, score)))


def embed_item_in_place(item: dict) -> None:
    """Attach transient (not persisted as-is) CLIP embeddings for scoring."""
    item["_textEmbedding"] = embed_text(_text_blob(item))
    item["_imageEmbedding"] = embed_image_url(item.get("imageUrl"))

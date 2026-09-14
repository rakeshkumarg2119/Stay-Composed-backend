"""
CLIP-based text + image embedding service.

Uses sentence-transformers' CLIP wrapper (`clip-ViT-B-32` by default) so a
single model can embed both the free-text report and the uploaded photo into
the same vector space, which is what lets us rank lost/found candidates by
combined text+image cosine similarity instead of naive keyword overlap.

The model is lazy-loaded once per process (first request pays the cost).
"""

import io
import logging
from functools import lru_cache

import numpy as np
import requests
from PIL import Image

from app.config import get_settings

logger = logging.getLogger("clip_service")


@lru_cache
def _get_model():
    # Imported lazily so the rest of the app can boot even before torch/
    # sentence-transformers finish downloading weights on first run.
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    logger.info("Loading CLIP model: %s", settings.clip_model_name)
    return SentenceTransformer(settings.clip_model_name)


def embed_text(text: str) -> np.ndarray | None:
    text = (text or "").strip()
    if not text:
        return None
    model = _get_model()
    vec = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
    return vec


def embed_image_url(image_url: str | None) -> np.ndarray | None:
    if not image_url:
        return None
    try:
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        logger.warning("Could not fetch/decode image for embedding: %s", image_url)
        return None

    model = _get_model()
    vec = model.encode([img], convert_to_numpy=True, normalize_embeddings=True)[0]
    return vec


def cosine_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    # Vectors are already L2-normalized by encode(..., normalize_embeddings=True)
    return float(np.dot(a, b))

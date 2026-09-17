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
import time
from functools import lru_cache

import os
import ssl
import urllib3
import numpy as np
import requests
from PIL import Image

from app.config import get_settings

logger = logging.getLogger("clip_service")


@lru_cache
def _get_model():
    # Handle campus / corporate proxy certificates on Windows (e.g. tcarts.in)
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib3.disable_warnings()
        old_init = requests.Session.__init__
        def _insecure_init(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self.verify = False
        requests.Session.__init__ = _insecure_init
    except Exception:
        pass

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


def embed_image_url(image_url: str | None, retries: int = 3, backoff_seconds: float = 1.5) -> np.ndarray | None:
    if not image_url:
        return None

    img = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(image_url, timeout=10)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            break
        except Exception:
            # Most common cause: this runs right after the file was
            # uploaded to Cloudinary, and the CDN hasn't finished
            # propagating the asset yet, so the very next fetch 404s or
            # times out. Retrying with a short backoff avoids permanently
            # baking a missing imageEmbedding (and losing up to 35 of 98
            # match-score points, more than the chat gate itself) into the
            # stored item over what's usually a sub-5-second timing gap.
            if attempt == retries:
                logger.warning("Could not fetch/decode image for embedding after %d attempts: %s", retries, image_url)
                return None
            time.sleep(backoff_seconds * attempt)

    model = _get_model()
    vec = model.encode([img], convert_to_numpy=True, normalize_embeddings=True)[0]
    return vec


def cosine_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    # Vectors are already L2-normalized by encode(..., normalize_embeddings=True)
    return float(np.dot(a, b))
"""
Sentence-embedding service for pure text-to-text semantic comparison
(secret-answer verification in app/routers/claims.py).

Deliberately a SEPARATE model from app/services/clip_service.py's CLIP
model. CLIP's text tower is anisotropic — it was trained to align captions
with images, not to discriminate unrelated sentences from each other — so
two completely unrelated short phrases ("no" vs. "Hairline scratch near
HDMI port") routinely land at 0.6-0.85 cosine similarity in CLIP text
space just from the shape of the embedding space, regardless of meaning.
A flat 0.68 threshold on that is close to a coin flip, which is what let
wrong answers pass verification.

`all-MiniLM-L6-v2` is trained on NLI/STS sentence-similarity objectives,
so unrelated pairs cluster much closer to 0 and genuine paraphrases stay
clearly separated — a fixed threshold actually means something here.

MODEL_VERSION is stored alongside every embedding this service produces
(see items.py) so claims.py can tell, per found-item record, whether its
stored secretAnswerEmbeddings were built with this model. Existing found
items only ever stored a bcrypt hash of the answer text, never the
plaintext, so there is no way to retroactively re-embed them if the model
changes again later — bump MODEL_VERSION whenever you swap models, and
claims.py will correctly skip Tier 2 (fall back to exact-hash only) for
any record whose stored version doesn't match, rather than comparing
across two incompatible vector spaces.
"""

from functools import lru_cache

import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"
MODEL_VERSION = "minilm-l6-v2"


@lru_cache
def _get_text_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME)


def embed_answer_text(text: str) -> np.ndarray | None:
    text = (text or "").strip()
    if not text:
        return None
    model = _get_text_model()
    return model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]


def cosine_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    if a is None or b is None:
        return None
    # Vectors are already L2-normalized by encode(..., normalize_embeddings=True)
    return float(np.dot(a, b))

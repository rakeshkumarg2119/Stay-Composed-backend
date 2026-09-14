import re

import bcrypt


def _normalize(answer: str) -> str:
    """Lowercase + collapse whitespace so trivial formatting differences don't fail a claim."""
    return re.sub(r"\s+", " ", answer.strip().lower())


def hash_secret(value: str) -> str:
    """Hash a single secret detail / challenge answer. Never store the plaintext."""
    normalized = _normalize(value)[:72]  # bcrypt's own input limit
    return bcrypt.hashpw(normalized.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(candidate_answer: str, stored_hash: str) -> bool:
    try:
        normalized = _normalize(candidate_answer)[:72]
        return bcrypt.checkpw(normalized.encode("utf-8"), stored_hash.encode("utf-8"))
    except ValueError:
        return False


def mask_display_name(full_name: str | None, email: str | None) -> str:
    """Never expose department, roll number, or phone in-app — just a masked display name."""
    if full_name:
        first = full_name.strip().split(" ")[0]
        return f"Campus Member {first}"
    if email:
        local = email.split("@")[0]
        return f"Campus Member {local[:3].upper()}***"
    return "Campus Member"


_TAG_RE = re.compile(r"<[^>]*>")


def sanitize_chat_text(text: str, max_length: int = 1000) -> str:
    """
    Basic (not end-to-end encrypted) message hygiene: strip any HTML/script tags
    so a message can't inject markup into the other person's chat view, collapse
    whitespace, and hard-cap length. Transport security (TLS) + auth checks on
    the endpoints are what actually protect the message in transit/at rest.
    """
    stripped = _TAG_RE.sub("", text)
    collapsed = re.sub(r"\s+", " ", stripped).strip()
    return collapsed[:max_length]

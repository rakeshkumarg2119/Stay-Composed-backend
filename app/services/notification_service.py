"""
Single entry point for "tell a user something happened".

Every user-facing event MUST go through notify() rather than calling
send_push_to_email directly. Reason: a push is fire-and-forget — it can
fail (no token registered, token rotated, permission denied, phone
offline, FCM outage) and once it fails the event is gone forever. The
in-app feed read by GET /notifications is the durable record, and for a
long time it was empty for every event except claim verification, because
items.py and chat.py were calling the push layer straight and never
writing a row. Routing both through one function is what stops those two
paths drifting apart again.

Write-first ordering is deliberate: the feed row is inserted BEFORE the
push is attempted, so a dead FCM token can never cost the user the
notification history entry.
"""

import logging
from datetime import datetime, timezone

from app.database import notifications_collection
from app.services.push_service import send_push_to_email

logger = logging.getLogger("notification_service")

# Canonical event types. These are the single source of truth for the
# `type` string — the Flutter client's NotificationType enum mirrors this
# list exactly, and notifications_provider.dart's _parseType() converts
# snake_case -> camelCase to match. Adding a value here without adding the
# camelCase twin to enums.dart makes the client silently fall back to
# matchFound instead of rendering/routing correctly.
TYPE_MATCH_FOUND = "match_found"
TYPE_CHAT_OPENED = "chat_opened"
TYPE_CHAT_MESSAGE = "chat_message"
TYPE_VERIFICATION_COMPLETED = "verification_completed"
TYPE_VERIFICATION_FAILED = "verification_failed"
TYPE_BLOOD_REQUEST_CREATED = "blood_request_created"
TYPE_BLOOD_REQUEST_UPDATED = "blood_request_updated"

KNOWN_TYPES = {
    TYPE_MATCH_FOUND,
    TYPE_CHAT_OPENED,
    TYPE_CHAT_MESSAGE,
    TYPE_VERIFICATION_COMPLETED,
    TYPE_VERIFICATION_FAILED,
    TYPE_BLOOD_REQUEST_CREATED,
    TYPE_BLOOD_REQUEST_UPDATED,
}


async def notify(
    email: str,
    *,
    type_: str,
    title: str,
    body: str,
    related_id: str,
    extra: dict[str, str] | None = None,
) -> None:
    """
    Persists the in-app notification row, then fires the push.

    `type_` must be snake_case and present in KNOWN_TYPES. `extra` is
    merged into the FCM data payload only (deep-link hints like
    complaintId / foundItemId) — it is NOT stored on the feed row, which
    keeps exactly the shape NotificationOut / AppNotification expect.

    Never raises. A notification failure must not break the request that
    triggered it (item creation, chat message, claim submission).
    """
    if type_ not in KNOWN_TYPES:
        # Not fatal — but this is exactly the drift that made every
        # notification parse as the fallback type on the client.
        logger.warning("notify() called with unknown type %r — client will fall back.", type_)

    try:
        await notifications_collection().insert_one(
            {
                "email": email,
                "type": type_,
                "title": title,
                "body": body,
                "createdAt": datetime.now(timezone.utc),
                "isRead": False,
                "relatedId": related_id,
            }
        )
    except Exception:
        logger.exception("Failed writing notification row for %s (%s)", email, type_)

    data: dict[str, str] = {"type": type_, "relatedId": related_id}
    if extra:
        data.update(extra)

    try:
        await send_push_to_email(email, title=title, body=body, data=data)
    except Exception:
        # send_push_to_email already fails soft internally; this is a
        # belt-and-braces guard in case that contract changes later.
        logger.exception("Failed sending push for %s (%s)", email, type_)

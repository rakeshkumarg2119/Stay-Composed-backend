"""
Firebase Cloud Messaging send layer.

Requires FIREBASE_CREDENTIALS_PATH (see config.py) pointing at a service-
account private-key JSON downloaded from Firebase Console -> Project
Settings -> Service Accounts -> Generate new private key. NOT the same
file as the Flutter app's google-services.json — that one is client-side
only and firebase-admin can't use it.

Every function here fails soft: a push failure is logged and swallowed,
never raised, so a notification bug can't take down the request that
triggered it (item creation, chat message, etc).
"""

import logging

import firebase_admin
from firebase_admin import credentials, messaging

from app.config import get_settings

logger = logging.getLogger("push_service")

_app: firebase_admin.App | None = None


def _get_app() -> firebase_admin.App | None:
    global _app
    if _app is not None:
        return _app

    settings = get_settings()
    if not settings.firebase_credentials_path:
        logger.warning("FIREBASE_CREDENTIALS_PATH not set — push notifications are a no-op.")
        return None

    try:
        cred = credentials.Certificate(settings.firebase_credentials_path)
        _app = firebase_admin.initialize_app(cred)
        return _app
    except Exception:
        logger.exception("Failed to initialize firebase-admin — check FIREBASE_CREDENTIALS_PATH.")
        return None


async def send_push(
    token: str,
    *,
    title: str,
    body: str,
    data: dict[str, str] | None = None,
) -> bool:
    """
    Sends one push to one device token. `data` is a flat string-to-string
    map delivered as the message's data payload — the client reads it in
    the foreground/background/terminated handlers to route taps (see
    AppNotification.type / relatedId). All values MUST be strings; FCM
    rejects non-string data values.
    """
    app = _get_app()
    if app is None:
        return False

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=data or {},
        android=messaging.AndroidConfig(priority="high"),
    )

    try:
        # firebase-admin's send() is synchronous (blocking HTTP under the
        # hood) — fine at this app's message volume; move to a thread pool
        # executor if this ever becomes a bottleneck under load.
        messaging.send(message)
        return True
    except messaging.UnregisteredError:
        # Token is dead (app uninstalled, token rotated and old one never
        # cleaned up) — caller should remove it from device_tokens on this
        # specific error, not on generic failures.
        logger.info("FCM token unregistered, should be pruned: %s...", token[:16])
        raise
    except Exception:
        logger.exception("FCM send failed for token %s...", token[:16])
        return False


async def send_push_to_email(email: str, *, title: str, body: str, data: dict[str, str] | None = None) -> int:
    """
    Looks up every device token stored for this email (multi-device) and
    sends to each. Prunes tokens FCM reports as unregistered. Returns the
    number of successful sends.
    """
    from app.database import device_tokens_collection  # local import avoids a circular import at module load

    coll = device_tokens_collection()
    tokens = [d["token"] async for d in coll.find({"email": email})]
    if not tokens:
        return 0

    sent = 0
    for token in tokens:
        try:
            ok = await send_push(token, title=title, body=body, data=data)
            if ok:
                sent += 1
        except messaging.UnregisteredError:
            await coll.delete_one({"token": token})
    return sent

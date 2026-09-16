import logging

import aiosmtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger("email_service")


def _build_message(to_addr: str, subject: str, body: str) -> EmailMessage:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


async def send_blood_alert(recipients: list[str], student_name: str, blood_type: str, phone_number: str, sender_email: str) -> int:
    """
    Broadcasts to EVERY address in `recipients` — this is a campus-wide alert,
    not scoped to a single department, so a rare blood type reaches whoever
    across campus might be able to help.
    """
    settings = get_settings()
    subject = f"🩸 Urgent Blood Requirement: {blood_type} needed — Stay Composed Alert"
    body = (
        f"A campus member has raised an urgent blood donation request.\n\n"
        f"Patient / Student: {student_name}\n"
        f"Blood Type Needed: {blood_type}\n"
        f"Contact Number: {phone_number}\n"
        f"Raised by (verified campus account): {sender_email}\n\n"
        f"If you are available and eligible to donate, please call the number above directly.\n"
        f"This alert was broadcast campus-wide across all departments.\n"
    )

    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP credentials not configured — skipping actual send (dry run).")
        return len(recipients)

    sent = 0
    async with aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port, start_tls=True) as smtp:
        await smtp.login(settings.smtp_user, settings.smtp_password)
        for addr in recipients:
            try:
                await smtp.send_message(_build_message(addr, subject, body))
                sent += 1
            except Exception:
                logger.exception("Failed to send blood alert to %s", addr)
    return sent


async def send_match_found_email(
    to_addr: str,
    *,
    is_lost_reporter: bool,
    other_item_title: str,
    confidence: int,
) -> bool:
    """
    Fired once per new match crossing chat_min_confidence — see
    app/routers/items.py `create_item`. One email per matched party per
    match, not a digest; dedup/rate-limiting is the caller's job if this
    turns out to be too noisy in practice.
    """
    settings = get_settings()
    role = "lost item" if is_lost_reporter else "found item"
    subject = f"🔎 Possible match found for your {role} — Stay Composed"
    body = (
        f"A possible match was found for your report.\n\n"
        f"Matched against: {other_item_title}\n"
        f"Match confidence: {confidence}%\n\n"
        f"Open the app to view details and start a chat with the other party "
        f"to coordinate verification.\n"
    )

    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("SMTP credentials not configured — skipping match email (dry run) to %s", to_addr)
        return False

    try:
        async with aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port, start_tls=True) as smtp:
            await smtp.login(settings.smtp_user, settings.smtp_password)
            await smtp.send_message(_build_message(to_addr, subject, body))
        return True
    except Exception:
        logger.exception("Failed to send match-found email to %s", to_addr)
        return False
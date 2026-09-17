import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.database import chat_messages_collection, chat_threads_collection, items_collection
from app.models import ChatMessageOut, ChatTemplatesOut, ChatThreadOut, ChatThreadRequest
from app.security import sanitize_chat_text
from app.services.matching import score_pair
from app.services.moderation import TIER_RESPONSES, classify_message
from app.services.notification_service import TYPE_CHAT_MESSAGE, TYPE_CHAT_OPENED, notify

router = APIRouter(prefix="/chat", tags=["chat"])

logger = logging.getLogger("chat")

# Consecutive "held" messages from the same sender before the whole
# conversation gets auto-frozen and routed to admin review.
HELD_ESCALATION_THRESHOLD = 3

# Presence heartbeat. WebSocketDisconnect only fires on a *clean* close.
# A backgrounded phone, a locked screen, a dropped mobile network, or a
# proxy silently killing an idle socket never sends a close frame — so
# without an active liveness check, a party who has actually gone offline
# stays in the "online" registry forever (or until the OS eventually
# times out the TCP connection, which can be many minutes). We instead
# ping every connection on an interval and prune anything that fails to
# respond within the timeout window.
HEARTBEAT_INTERVAL_SECONDS = 20
PRESENCE_TIMEOUT_SECONDS = 45


# ---------------------------------------------------------------------------
# In-memory WebSocket registry (per-process). Fine for a single backend
# instance; a multi-instance deploy would need a shared pub/sub (e.g. Redis)
# instead of this dict.
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self._threads: dict[str, list[tuple[WebSocket, str]]] = {}
        self._last_seen: dict[WebSocket, datetime] = {}
        self._heartbeat_task: asyncio.Task | None = None

    async def connect(self, thread_id: str, ws: WebSocket, email: str) -> None:
        await ws.accept()
        self._threads.setdefault(thread_id, []).append((ws, email))
        self._last_seen[ws] = datetime.now(timezone.utc)
        self.start_heartbeat()  # idempotent — guaranteed to run without depending on app-startup wiring
        online_emails = self.get_online_emails(thread_id)
        # Send initial presence frame to newly connected socket
        try:
            await ws.send_json({
                "type": "presence",
                "onlineEmails": online_emails,
                "userEmail": email,
                "status": "online",
            })
        except Exception:
            pass
        # Broadcast presence to thread participants
        await self.broadcast(thread_id, {
            "type": "presence",
            "onlineEmails": online_emails,
            "userEmail": email,
            "status": "online",
        })

    def disconnect(self, thread_id: str, ws: WebSocket) -> str | None:
        conns = self._threads.get(thread_id, [])
        disconnected_email = None
        remaining = []
        for sock, email in conns:
            if sock == ws:
                disconnected_email = email
            else:
                remaining.append((sock, email))
        if remaining:
            self._threads[thread_id] = remaining
        elif thread_id in self._threads:
            del self._threads[thread_id]
        self._last_seen.pop(ws, None)
        return disconnected_email

    def get_online_emails(self, thread_id: str) -> list[str]:
        return list({email for _, email in self._threads.get(thread_id, [])})

    def is_online(self, thread_id: str, email: str) -> bool:
        return email in self.get_online_emails(thread_id)

    async def broadcast(self, thread_id: str, payload: dict) -> None:
        for ws, _ in list(self._threads.get(thread_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(thread_id, ws)

    def touch(self, ws: WebSocket) -> None:
        """Call whenever anything is heard from this socket (a real message
        or just a heartbeat pong) so it isn't reaped as stale."""
        if ws in self._last_seen:
            self._last_seen[ws] = datetime.now(timezone.utc)

    async def _sweep_once(self) -> None:
        now = datetime.now(timezone.utc)
        for thread_id, conns in list(self._threads.items()):
            for ws, email in list(conns):
                last = self._last_seen.get(ws, now)
                if (now - last).total_seconds() > PRESENCE_TIMEOUT_SECONDS:
                    # No pong / activity within the timeout — treat as gone.
                    disconnected_email = self.disconnect(thread_id, ws)
                    try:
                        await ws.close(code=4408)  # 4408 = timeout
                    except Exception:
                        pass
                    if disconnected_email:
                        await self.broadcast(thread_id, {
                            "type": "presence",
                            "onlineEmails": self.get_online_emails(thread_id),
                            "userEmail": disconnected_email,
                            "status": "offline",
                        })
                    continue
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    # The send itself failed — socket is dead right now,
                    # don't wait for the timeout window.
                    disconnected_email = self.disconnect(thread_id, ws)
                    if disconnected_email:
                        await self.broadcast(thread_id, {
                            "type": "presence",
                            "onlineEmails": self.get_online_emails(thread_id),
                            "userEmail": disconnected_email,
                            "status": "offline",
                        })

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self._sweep_once()
            except Exception:
                logger.exception("Presence heartbeat sweep failed")

    def start_heartbeat(self) -> None:
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def stop_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None


manager = ConnectionManager()

# Pre-verification template messages tailored for both Finder and Loster (Claimant)
# Both questions and answers are allowlisted so parties can converse safely
# before secret challenge verification is completed.
PRE_VERIFICATION_QUESTIONS_LOSTER = [
    "Where exactly did you find it?",
    "What time did you find it?",
    "Can you describe the item's condition?",
    "Can you share a safe public meeting point?",
    "Are you ready to initiate the verification challenge?",
]

PRE_VERIFICATION_ANSWERS_LOSTER = [
    "I lost it on campus earlier today.",
    "I lost it near the library / canteen area.",
    "It has my personal marks and contents inside.",
    "I can verify the secret challenge questions.",
    "Yes, I am available to meet and verify.",
]

PRE_VERIFICATION_QUESTIONS_FINDER = [
    "Can you describe key details or unique marks on the item?",
    "When and where approximately did you lose it?",
    "What brand, color, or model is the item?",
    "Are you ready to answer the verification challenge?",
    "Can you share a safe public meeting point?",
]

PRE_VERIFICATION_ANSWERS_FINDER = [
    "I found it near the campus grounds / academic block.",
    "I found it earlier today and kept it safe.",
    "The item is in good condition and kept securely.",
    "Let's coordinate at a campus security desk or public spot.",
    "Please answer the verification challenge so we can proceed.",
]

PRE_VERIFICATION_MESSAGES = frozenset(
    PRE_VERIFICATION_QUESTIONS_LOSTER
    + PRE_VERIFICATION_ANSWERS_LOSTER
    + PRE_VERIFICATION_QUESTIONS_FINDER
    + PRE_VERIFICATION_ANSWERS_FINDER
    # Backwards compatibility with previous initial templates
    + ["Can you describe the item?"]
)


def _thread_id(complaint_id: str, found_item_id: str) -> str:
    return f"{complaint_id}:{found_item_id}"


def _msg_out(doc: dict) -> ChatMessageOut:
    return ChatMessageOut(
        id=doc["_id"],
        threadId=doc["threadId"],
        senderEmail=doc["senderEmail"],
        text=doc["text"],
        sentAt=doc["sentAt"],
    )


async def _get_thread_or_404(thread_id: str) -> dict:
    thread = await chat_threads_collection().find_one({"_id": thread_id})
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found.")
    return thread


def _require_participant(thread: dict, email: str) -> None:
    if email not in (thread["claimantEmail"], thread["founderEmail"]):
        raise HTTPException(status_code=403, detail="You are not part of this chat.")


@router.post("/thread", response_model=ChatThreadOut)
async def get_or_create_thread(payload: ChatThreadRequest):
    """
    Only ever creates/returns a thread when the AI match confidence between
    this specific lost complaint and found item is >= chat_min_confidence —
    the "exact requirement match" gate. Below that, no thread, no chat.
    """
    settings = get_settings()
    coll = items_collection()

    complaint = await coll.find_one({"_id": payload.complaintId, "type": "lost"})
    if not complaint:
        raise HTTPException(status_code=404, detail="Lost complaint not found.")
    found = await coll.find_one({"_id": payload.foundItemId, "type": "found"})
    if not found:
        raise HTTPException(status_code=404, detail="Found item not found.")

    claimant_email = complaint["reporterEmail"]
    founder_email = found["reporterEmail"]
    claimant_name = complaint.get("reporterName") or complaint.get("reportedBy") or "Item Owner"
    founder_name = found.get("reporterName") or found.get("reportedBy") or "Item Finder"
    if payload.requesterEmail not in (claimant_email, founder_email):
        raise HTTPException(status_code=403, detail="You are not associated with either report.")

    thread_id = _thread_id(payload.complaintId, payload.foundItemId)
    existing = await chat_threads_collection().find_one({"_id": thread_id})
    if existing:
        updates = {}
        if not existing.get("claimantName"):
            updates["claimantName"] = claimant_name
            existing["claimantName"] = claimant_name
        if not existing.get("founderName"):
            updates["founderName"] = founder_name
            existing["founderName"] = founder_name
        if updates:
            await chat_threads_collection().update_one({"_id": thread_id}, {"$set": updates})
        return ChatThreadOut(threadId=thread_id, **{k: v for k, v in existing.items() if k != "_id"})

    lost_vecs = {
        "category": complaint.get("category"),
        "location": complaint.get("location"),
        "_textEmbedding": np.array(complaint["textEmbedding"]) if complaint.get("textEmbedding") else None,
        "_imageEmbedding": np.array(complaint["imageEmbedding"]) if complaint.get("imageEmbedding") else None,
    }
    found_vecs = {
        "category": found.get("category"),
        "location": found.get("location"),
        "_textEmbedding": np.array(found["textEmbedding"]) if found.get("textEmbedding") else None,
        "_imageEmbedding": np.array(found["imageEmbedding"]) if found.get("imageEmbedding") else None,
    }
    confidence = score_pair(lost_vecs, found_vecs)

    if confidence < settings.chat_min_confidence:
        raise HTTPException(
            status_code=403,
            detail=f"AI match confidence ({confidence}) hasn't reached the chat threshold "
                   f"({settings.chat_min_confidence}) yet.",
        )

    now = datetime.now(timezone.utc)
    doc = {
        "_id": thread_id,
        "complaintId": payload.complaintId,
        "foundItemId": payload.foundItemId,
        "claimantEmail": claimant_email,
        "founderEmail": founder_email,
        "claimantName": claimant_name,
        "founderName": founder_name,
        "confidence": confidence,
        "status": "chat",
        "createdAt": now,
        "verificationStartedAt": None,
        "heldMessageCount": 0,
    }
    try:
        await chat_threads_collection().insert_one(doc)
    except DuplicateKeyError:
        # Two near-simultaneous requests for the same pair (React 18 /
        # Next.js dev-mode StrictMode double-invokes effects, so ChatPanel's
        # setup() can genuinely fire twice) can both pass the `existing`
        # check above before either has inserted. The loser here isn't a
        # real failure — the thread now exists, just insert this one
        # created it. Return the winner's doc instead of 500ing.
        winner = await chat_threads_collection().find_one({"_id": thread_id})
        if winner:
            return ChatThreadOut(threadId=thread_id, **{k: v for k, v in winner.items() if k != "_id"})
        raise

    # NOTE: no email here on purpose. A thread only ever gets created once
    # confidence >= chat_min_confidence — the exact same threshold
    # app/routers/items.py::_notify_new_matches uses to decide whether to
    # email both parties, and that function runs unconditionally the
    # moment the later-created of these two items exists (scanning every
    # already-open item of the opposite type). So by the time a thread can
    # even be created here, the founder has already been emailed about
    # this exact match once. Emailing them again here was a pure duplicate
    # (this used to call send_match_found_email(founder_email, ...) — see
    # git history if you need the previous version). The push below is
    # kept because "someone opened a chat with you right now" is a
    # genuinely distinct, timely signal that the match email doesn't
    # carry — the match email could be from days ago.
    try:
        await notify(
            founder_email,
            type_=TYPE_CHAT_OPENED,
            title="Someone wants to chat about your found item",
            body=f"A claimant started a chat about: {found['title']}",
            related_id=thread_id,
            extra={
                "complaintId": payload.complaintId,
                "foundItemId": payload.foundItemId,
            },
        )
    except Exception:
        logger.exception("Failed notifying founder of chat-opened for thread %s", thread_id)

    return ChatThreadOut(threadId=thread_id, **{k: v for k, v in doc.items() if k != "_id"})


@router.get("/{thread_id}/templates", response_model=ChatTemplatesOut)
async def get_templates(thread_id: str, email: str = Query(...)):
    """
    Full pre-verification template list for this participant's role.
    Previously the frontend only ever learned about templates from the
    'allowedMessages' field on a *rejected*-message error, which is why it
    only ever had a partial/stale list to render (and nothing to render
    before the user had already been rejected once). This endpoint lets
    the client fetch the complete list up front, any time, so it can be
    rendered as a proper scrollable picker. Free text is still allowed
    alongside these — see the websocket handler.
    """
    thread = await _get_thread_or_404(thread_id)
    _require_participant(thread, email)

    if email == thread["claimantEmail"]:
        role, questions, answers = "claimant", PRE_VERIFICATION_QUESTIONS_LOSTER, PRE_VERIFICATION_ANSWERS_LOSTER
    else:
        role, questions, answers = "founder", PRE_VERIFICATION_QUESTIONS_FINDER, PRE_VERIFICATION_ANSWERS_FINDER

    return ChatTemplatesOut(role=role, questions=questions, answers=answers, freeTextAllowed=True)


@router.get("/{thread_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(thread_id: str, email: str = Query(...)):
    thread = await _get_thread_or_404(thread_id)
    _require_participant(thread, email)
    docs = [d async for d in chat_messages_collection().find({"threadId": thread_id}).sort("sentAt", 1)]
    return [_msg_out(d) for d in docs]


@router.get("/my-threads", response_model=list[ChatThreadOut])
async def my_threads(email: str = Query(...)):
    """
    All chat threads this email is part of, either side — this is how a
    founder discovers that a claimant hit the 85%+ gate and opened a chat on
    one of their found items (the founder gets no other notification of it).
    """
    docs = [
        d
        async for d in chat_threads_collection()
        .find({"$or": [{"claimantEmail": email}, {"founderEmail": email}]})
        .sort("createdAt", -1)
    ]
    return [ChatThreadOut(threadId=d["_id"], **{k: v for k, v in d.items() if k != "_id"}) for d in docs]


@router.post("/{thread_id}/start-verification", response_model=ChatThreadOut)
async def start_verification(thread_id: str, founder_email: str = Query(...)):
    """
    Founder-only. Locks the chat permanently and hands off to the existing
    hashed challenge-question claim flow (POST /claims) — chat does not
    reopen after this, matching the "structured verification only" rule.
    """
    thread = await _get_thread_or_404(thread_id)
    if founder_email != thread["founderEmail"]:
        raise HTTPException(status_code=403, detail="Only the founder who reported this item can start verification.")
    if thread["status"] != "chat":
        raise HTTPException(status_code=409, detail=f"Thread is already '{thread['status']}', can't start verification again.")

    now = datetime.now(timezone.utc)
    await chat_threads_collection().update_one(
        {"_id": thread_id},
        {"$set": {"status": "verifying", "verificationStartedAt": now}},
    )
    await manager.broadcast(
        thread_id,
        {"event": "phase_changed", "status": "verification_pending"},
    )
    await manager.broadcast(thread_id, {"type": "verification_started", "startedAt": now.isoformat()})

    updated = await chat_threads_collection().find_one({"_id": thread_id})
    return ChatThreadOut(threadId=thread_id, **{k: v for k, v in updated.items() if k != "_id"})


@router.post("/{thread_id}/complete-handover", response_model=ChatThreadOut)
async def complete_handover(thread_id: str, email: str = Query(...)):
    """
    Called by either the founder or claimant once physical handover is complete.
    Permanently marks items as handed_over and closes the chat thread.
    """
    thread = await _get_thread_or_404(thread_id)
    _require_participant(thread, email)
    if thread.get("status") in ("handed_over", "closed"):
        raise HTTPException(status_code=400, detail="Handover is already completed.")

    now = datetime.now(timezone.utc)
    await chat_threads_collection().update_one(
        {"_id": thread_id},
        {"$set": {"status": "handed_over", "handedOverAt": now, "handedOverBy": email}},
    )
    await items_collection().update_one(
        {"_id": thread["foundItemId"]},
        {"$set": {"status": "resolved", "handedOverAt": now}},
    )
    await items_collection().update_one(
        {"_id": thread["complaintId"]},
        {"$set": {"status": "resolved", "handedOverAt": now}},
    )
    await manager.broadcast(thread_id, {
        "event": "phase_changed",
        "status": "handed_over",
    })
    await manager.broadcast(thread_id, {
        "type": "handover_completed",
        "completedBy": email,
        "handedOverAt": now.isoformat(),
    })

    updated = await chat_threads_collection().find_one({"_id": thread_id})
    return ChatThreadOut(threadId=thread_id, **{k: v for k, v in updated.items() if k != "_id"})


@router.websocket("/ws/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: str, email: str = Query(...)):
    settings = get_settings()
    thread = await chat_threads_collection().find_one({"_id": thread_id})
    if not thread or email not in (thread["claimantEmail"], thread["founderEmail"]):
        await websocket.close(code=4403)
        return

    await manager.connect(thread_id, websocket, email)
    try:
        while True:
            data = await websocket.receive_json()
            manager.touch(websocket)  # any frame (including a client pong) counts as "alive"
            text = sanitize_chat_text(str(data.get("text", "")), settings.chat_message_max_length)
            if not text:
                continue

            current = await chat_threads_collection().find_one({"_id": thread_id})
            if not current or current["status"] in ("handed_over", "closed", "resolved"):
                await websocket.send_json({"type": "error", "message": "This chat is closed — item handover has been completed."})
                continue

            if current["status"] == "frozen":
                await websocket.send_json(
                    {"type": "error", "message": "This conversation has been frozen and is under admin review."}
                )
                continue

            if current["status"] == "verifying":
                await websocket.send_json(
                    {"type": "error", "message": "Chat is locked while ownership verification is pending."}
                )
                continue

            # Pre-verification templates are offered as quick-reply suggestions
            # (see GET /chat/{thread_id}/templates) but are no longer the ONLY
            # thing a party can send — free text is allowed too, both before
            # and after verification. Moderation below still applies to both.
            if current["status"] not in ("chat", "verified"):
                await websocket.send_json(
                    {"type": "error", "message": "Messages are not allowed in the current chat phase."}
                )
                continue

            # Basic abuse guard: cap messages per sender per minute (not E2E crypto,
            # just a floor against spam/flooding on an authenticated, sanitized channel)
            window_start = datetime.now(timezone.utc) - timedelta(minutes=1)
            recent = await chat_messages_collection().count_documents(
                {"threadId": thread_id, "senderEmail": email, "sentAt": {"$gte": window_start}}
            )
            if recent >= settings.chat_max_messages_per_minute:
                await websocket.send_json({"type": "error", "message": "Slow down — too many messages, try again shortly."})
                continue

            # ------------------------------------------------------------------
            # Tone/abuse moderation — see app/services/moderation.py for the
            # tiered contract. Runs after the phase/rate checks above so we
            # only moderate messages that were otherwise going to be sent.
            # ------------------------------------------------------------------
            tier = classify_message(text)

            if tier == "frozen":
                await chat_threads_collection().update_one(
                    {"_id": thread_id}, {"$set": {"status": "frozen", "frozenAt": datetime.now(timezone.utc)}}
                )
                await manager.broadcast(thread_id, {"event": "phase_changed", "status": "frozen"})
                await manager.broadcast(
                    thread_id,
                    {"type": "conversation_frozen", "message": TIER_RESPONSES["frozen"]},
                )
                continue

            if tier == "held":
                new_count = current.get("heldMessageCount", 0) + 1
                update = {"$set": {"heldMessageCount": new_count}}
                await chat_threads_collection().update_one({"_id": thread_id}, update)

                if new_count >= HELD_ESCALATION_THRESHOLD:
                    await chat_threads_collection().update_one(
                        {"_id": thread_id}, {"$set": {"status": "frozen", "frozenAt": datetime.now(timezone.utc)}}
                    )
                    await manager.broadcast(thread_id, {"event": "phase_changed", "status": "frozen"})
                    await manager.broadcast(
                        thread_id,
                        {"type": "conversation_frozen", "message": TIER_RESPONSES["frozen"]},
                    )
                else:
                    await websocket.send_json(
                        {
                            "type": "moderation_notice",
                            "tier": "held",
                            "message": TIER_RESPONSES["held"],
                            "heldMessageCount": new_count,
                        }
                    )
                continue

            msg_doc = {
                "_id": f"msg-{uuid.uuid4().hex[:12]}",
                "threadId": thread_id,
                "senderEmail": email,
                "text": text,
                "sentAt": datetime.now(timezone.utc),
            }
            await chat_messages_collection().insert_one(msg_doc)
            await manager.broadcast(thread_id, {"type": "message", **{k: v for k, v in msg_doc.items() if k != "_id"}, "id": msg_doc["_id"], "sentAt": msg_doc["sentAt"].isoformat()})

            # Push only to the *other* participant, and only if they aren't
            # currently connected to this thread's socket — otherwise
            # they'd see the message live AND get a redundant push for the
            # same thing.
            recipient = current["founderEmail"] if email == current["claimantEmail"] else current["claimantEmail"]
            if not manager.is_online(thread_id, recipient):
                try:
                    sender_name = current.get("claimantName") if email == current["claimantEmail"] else current.get("founderName")
                    await notify(
                        recipient,
                        type_=TYPE_CHAT_MESSAGE,
                        title=sender_name or email,
                        body=text[:120],
                        related_id=thread_id,
                    )
                except Exception:
                    logger.exception("Failed notifying chat message for thread %s", thread_id)

            if tier == "nudge":
                await websocket.send_json(
                    {"type": "moderation_notice", "tier": "nudge", "message": TIER_RESPONSES["nudge"]}
                )
    except WebSocketDisconnect:
        disconnected_email = manager.disconnect(thread_id, websocket)
        if disconnected_email:
            await manager.broadcast(thread_id, {
                "type": "presence",
                "onlineEmails": manager.get_online_emails(thread_id),
                "userEmail": disconnected_email,
                "status": "offline",
            })
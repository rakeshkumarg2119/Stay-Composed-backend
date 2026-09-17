<div align="center">

<img src="stay_composed.png" alt="Stay Composed" width="260" height="260"/>

# 🛡️ Stay Composed — Backend

### *FastAPI + CLIP-Powered Campus Lost & Found and Emergency Assistance Engine*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![MongoDB](https://img.shields.io/badge/Database-MongoDB_Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![PyTorch CLIP](https://img.shields.io/badge/AI_Model-OpenAI_CLIP_ViT--B--32-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://github.com/openai/CLIP)
[![Firebase](https://img.shields.io/badge/Push-Firebase_Cloud_Messaging-FFCA28?style=for-the-badge&logo=firebase&logoColor=black)](https://firebase.google.com/)
[![WebSocket](https://img.shields.io/badge/Realtime-WebSockets-010101?style=for-the-badge&logo=socket.io&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)

**Recover • Verify • Stay Safe**

[✨ Key Features](#-key-features) • [🧠 AI Pipeline](#-how-the-ai-pipeline-works) • [🗂️ Project Structure](#️-project-structure) • [📡 API Reference](#-api-reference) • [🚀 Getting Started](#-getting-started) • [🔐 Security](#-security--privacy-commitments)

</div>

---

## 📌 Executive Summary

**Stay Composed Backend** is the FastAPI service powering privacy-first campus lost-and-found recovery, real-time claimant/founder chat, and emergency blood-alert broadcasting:

1. **Unlisted Found Vault** — found items are never publicly browsable; they surface only to a legitimate claimant whose lost report matches.
2. **CLIP Multimodal Embeddings** — text + image reports are embedded into one shared vector space so descriptions and photos can be ranked together.
3. **Two-Tier Ownership Verification** — bcrypt-hashed secret answers, with a semantic CLIP-similarity fallback so genuine owners aren't penalized for phrasing differences.
4. **Realtime Handover Chat** — WebSocket-based coordination chat that unlocks only once match confidence clears a threshold.
5. **Campus Blood Alert Network** — SMTP broadcast to a verified staff/student directory for urgent blood requests.
6. **Resilient Notification Layer** — every user-facing event is written to a durable in-app feed *before* a push is attempted, so a dead FCM token never costs a user their notification history.

---

## ✨ Key Features

### 🔍 1. True-Owner Matching Engine

- **Zero public browsing** — prevents bad actors from fabricating ownership claims by browsing found items.
- **Weighted scoring** — CLIP text similarity (up to 45 pts) + CLIP image similarity (up to 35 pts) + category match (12 pts) + location match (8 pts).
- **Ranked candidates, not a single verdict** — final ownership is *always* proven through the hashed secret-challenge flow, never by AI score alone.

### 🛡️ 2. Two-Tier Ownership Challenge

- Founders set 1–3 secret challenge questions; answers are **bcrypt-hashed at rest** — plaintext is never stored.
- **Tier 1:** exact bcrypt match. **Tier 2:** semantic CLIP cosine-similarity fallback for genuine claimants who phrase things differently.
- Rate-limited claim attempts with a cooldown lockout to prevent brute-forcing.

### 💬 3. Realtime Coordination Chat

- Native `WebSocket` endpoint per thread (`/chat/ws/{thread_id}`), gated behind a minimum match confidence.
- Quick-coordinate message templates per thread.
- `start-verification` and `complete-handover` transitions permanently lock and close resolved threads.

### 🔔 4. Durable, Write-First Notifications

- Single entry point (`notify()`) that **every** feature routes through — no router calls the push layer directly.
- The in-app feed row is written *before* the push attempt, so a missing/expired FCM device token can never silently erase a user's notification history.
- Push failures fail soft and are logged, never raised — a dead token can't break the request that triggered it (item creation, a chat message, a claim).

### 🩸 5. Campus Emergency Blood Alert System

- Instant SMTP broadcast (`aiosmtplib`) to a managed staff/student directory.
- Directory entries can be added/removed via a small admin API (`/blood-alert/staff`).

### 📲 6. Device Token Registry

- `/devices/register` keeps a live map of `email → FCM token` per platform (`ios` / `android`), refreshed on every app cold start and token rotation.

---

## 🧠 How the AI Pipeline Works

```
flowchart TD
    subgraph Registration
        A[Claimant: Files Lost Report] -->|Title + Desc| B[CLIP Text Embedding]
        C[Finder: Registers Found Item] -->|Photo Upload| D[CLIP Image Embedding]
        C -->|Title + Desc| E[CLIP Text Embedding]
        C -->|Secret Answers| F[Hashed via bcrypt + CLIP Vector Embedding]
    end

    subgraph Neural Matching
        B & D & E --> G[Weighted Cosine Similarity Engine]
        G -->|Confidence Score| H{Score >= threshold?}
        H -- No --> I[Listed as Pending Match]
        H -- Yes --> J[Surface Candidate + Enable Secure Chat]
    end

    subgraph Verification Flow
        J --> K[Founder Starts Verification]
        K --> L[Claimant Submits Challenge Answers]
        L --> M{Tier 1: bcrypt Exact Match?}
        M -- Yes --> P[Matched]
        M -- No --> N{Tier 2: CLIP Cosine Sim >= threshold?}
        N -- Yes --> P
        N -- No --> Q[Failed Attempt Counter + Cooldown]
        P --> R{Majority Passed?}
        R -- Yes --> S[✅ Status: Verified]
        R -- No --> T[❌ Retry / Lockout Cooldown]
    end

    subgraph Notify & Handover
        S --> NOTIFY[notify(): write in-app feed row, then attempt push]
        NOTIFY --> U[Chat Remains Open for Meeting]
        U --> V[Physical Handover Completed]
        V --> W[🎉 Items Marked Resolved & Thread Closed]
    end
```

---

## 🗂️ Project Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI app, CORS, router registration, startup indexes
│   ├── config.py                  # Pydantic settings — Mongo, SMTP, CLIP, matching, FCM
│   ├── database.py                # Motor/MongoDB Atlas connection + index management
│   ├── models.py                  # Pydantic request/response schemas
│   ├── security.py                # bcrypt hashing + answer normalization
│   ├── utils/
│   │   └── locations.py           # Campus location helpers
│   ├── routers/
│   │   ├── items.py                # Lost & found CRUD + candidate surfacing
│   │   ├── claims.py               # Two-tier ownership verification
│   │   ├── chat.py                 # WebSocket manager, verification + handover lifecycle
│   │   ├── devices.py              # FCM device token registration
│   │   ├── notifications.py        # In-app notification feed (GET /notifications, mark-read)
│   │   └── blood_alert.py          # Blood request creation + staff directory admin
│   └── services/
│       ├── clip_service.py         # Lazy-loaded CLIP model — text & image embeddings
│       ├── matching.py             # Weighted cosine-similarity scoring
│       ├── notification_service.py # notify() — single entry point, write-first ordering
│       ├── push_service.py         # Firebase Admin SDK — fails soft, never raises
│       ├── email_service.py        # aiosmtplib SMTP delivery
│       └── moderation.py           # Content moderation helpers
└── requirements.txt
```

---

## 📡 API Reference

| Area | Method & Path | Purpose |
|---|---|---|
| **Items** | `POST /items` | Create a lost or found report; background-tasks candidate matching |
| | `GET /items/mine` | A user's own items + their surfaced candidate matches |
| | `DELETE /items/demo-reset` | Reset demo accounts' items |
| **Claims** | `POST /claims` | Submit a two-tier ownership verification attempt |
| **Chat** | `POST /chat/thread` | Get or create a thread between founder and claimant |
| | `GET /chat/{thread_id}/messages` | Fetch thread message history |
| | `GET /chat/{thread_id}/templates` | Quick-coordinate message templates |
| | `GET /chat/my-threads` | All threads for a user |
| | `POST /chat/{thread_id}/start-verification` | Founder initiates challenge verification |
| | `POST /chat/{thread_id}/complete-handover` | Mark handover complete, close the thread |
| | `WS /chat/ws/{thread_id}` | Realtime message socket |
| **Devices** | `POST /devices/register` | Register/refresh an FCM push token |
| | `DELETE /devices/register` | Unregister a push token |
| **Notifications** | `GET /notifications` | A user's durable in-app notification feed |
| | `POST /notifications/{id}/read` | Mark a notification read |
| **Blood Alert** | `POST /blood-alert` | Broadcast an emergency blood request |
| | `GET /blood-alert/mine` | A user's own blood alert history |
| | `GET /blood-alert/staff` | List directory recipients |
| | `POST /blood-alert/staff` | Add a directory recipient |
| | `DELETE /blood-alert/staff/{email}` | Remove a directory recipient |
| **Meta** | `GET /health` | Health check |

---

## 🚀 Getting Started

### Prerequisites

- **Python** `3.11` or `3.12`
- **MongoDB Atlas** cluster (or local MongoDB for development)
- **Firebase** service-account credentials (for push notifications)
- **SMTP** credentials (for blood alert email + verification mail)

### Setup

```bash
# Navigate to the backend
cd backend

# Create and activate a virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables (backend/.env)
# MONGODB_URI=...
# DB_NAME=stay_composed
# SMTP_HOST=smtp.gmail.com
# SMTP_USER=...
# SMTP_PASSWORD=...
# FIREBASE_CREDENTIALS_PATH=/path/to/service-account.json
# MATCH_MIN_CONFIDENCE=40
# CHAT_MIN_CONFIDENCE=40

# Run the API
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Visit **`http://localhost:8000/health`** to confirm the service is up. 🚀

> **Note:** `FIREBASE_CREDENTIALS_PATH` expects the **Firebase Admin SDK** service-account JSON (Project Settings → Service Accounts → Generate new private key) — this is *not* the Flutter app's `google-services.json`, which is client-only. Leaving it empty makes push a no-op everywhere without breaking the rest of the API.

---

## 🔐 Security & Privacy Commitments

> **Important**
> - **Zero plaintext secrets** — challenge answers and secret features are bcrypt-hashed; nothing is ever stored or logged in plaintext.
> - **Zero public found directory** — found items are never listable; they only ever surface as a candidate match to a genuine claimant.
> - **Fail-soft notification layer** — a dead push token, an FCM outage, or an SMTP failure is logged and swallowed, never allowed to break the request that triggered it.
> - **Write-before-push ordering** — the durable in-app feed row is always written before a push is attempted, so notification history can never be lost to a delivery failure.
> - **Rate-limited verification** — claim attempts are capped with a cooldown to prevent brute-forcing secret answers.

---

<div align="center">

Built with ❤️ for campus safety and student support.

**Stay Composed** • True Owner Verification System • 2026

</div>

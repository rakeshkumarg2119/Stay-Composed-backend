# Stay Composed

Campus lost-and-found and emergency-help platform, built for [Hackathon Name].
Two features, one identity: **help reaching the right person, fast — without panic.**

---

## 1. Lost & Found (AI-Verified)

### Core Flow
- A **lost-item report** (description + optional photo) and a **found-item report** (photo + description) are both logged into the system.
- Found items are **not publicly browsable by default** — they only surface when a matching lost-item complaint is raised.
- **AI matching (CLIP, text + image embeddings)** compares lost and found reports and generates **ranked candidate matches** with confidence scores, not a single yes/no.

### Verification (the core differentiator)
- To claim a match, the claimant must correctly answer a **secret-detail challenge** — an identifying detail about the item that only the true owner and the finder would know.
- **Multi-field verification**: more than one detail can be requested; a majority match is required, so a single lucky guess can't fake ownership.
- Secret details are **hashed at rest** — never stored or viewable in plaintext, even by admins.

### Chat & Safety
- Pre-verification exchange is **structured/prompt-based only** (no open free-text chat), so the claim flow can't be used as a pretext to start an unrelated conversation.
- **Identity masking**: department, roll number, and phone number are never shown in-app. Only a display name is visible.
- Suggested **public meeting points** (e.g. library entrance, admin block) instead of sharing personal location.
- **AI-based abuse/toxicity moderation** on claim chats, with tiered response:
  1. Mild hostility → in-app nudge to stay respectful
  2. Clear abuse → message held, sender warned, repeat offenses flagged
  3. Severe/threatening language → conversation frozen, routed to human/admin review
- **Chat closes permanently** once verification succeeds — no further contact between the two parties.
- **Scoped visibility**: a finder's account only shows items *they* reported finding — never other users' lost-item reports — preventing "fishing" for secret details.
- **Timeout/cooldown**: unresolved claims expire after a set window; the item reopens to other candidates, and the timed-out claimant enters a cooldown before re-attempting.
- **Rate-limited claim attempts** to prevent brute-forcing secret details across many tries.
- **Report button** available at every step, routed to human review — never auto-resolved for serious cases.
- **Real, college-login-backed accounts** (OAuth restricted to campus email domain) — display names are masked, but every account is traceable on the backend if misuse occurs.

---

## 2. Campus Blood-Donation Alert

### Flow
- A student (e.g. NCC/NSS) submits a request: **blood type, name, phone number**.
- The backend looks up the **relevant department's staff directory**.
- An **email alert** (SMTP) is broadcast to all staff in that department with the request details.
- Whoever is free responds directly to the student's provided phone number — no availability-tracking needed, just a reliable broadcast.

### Notes
- **SMS was considered but deferred** — India's TRAI/DLT registration requirement for programmatic SMS makes it infeasible in the current build window. Positioned as a **future update** once DLT registration is complete.
- **Email chosen for now**: no registration hurdle, uses existing college email addresses, demo-safe (a real email can be shown arriving live).

---

## Tech Stack

| Layer | Choice |
|---|---|
| Web frontend | Next.js (simple interface, OAuth login) |
| Mobile frontend | Flutter |
| Backend | FastAPI |
| Database | MongoDB |
| Image storage | Cloudinary |
| AI matching | CLIP (image + text embeddings) |
| Blood-alert delivery | SMTP (email) |

**Backend is shared** — one FastAPI service serves both the Next.js web app and the Flutter mobile app, so matching, verification, and chat logic exist in exactly one place.

---

## Design Principles

1. **Reveal only what's necessary to verify a claim — nothing more.** (Applies to both hashed secret details and identity masking.)
2. **The system picks the match, not the user.** No browsing or hand-picking who to contact — matches only arise from genuine lost/found reports.
3. **AI handles the routine cases; humans handle the escalated ones.** Moderation and disputes always have a human review path.
4. **Name and pitch lead with the problem, not the domain** — "Stay Composed" deliberately avoids naming the category, to avoid the "ahh, this already exists" reflex.

---

## Status

Built in a condensed 3-day hackathon window. Core priority order:
1. Report + matching flow
2. Verification (hashed, multi-field)
3. Structured chat + auto-close
4. Blood-donation alert (lightweight, added once core loop is stable)
5. Abuse moderation + timeout/cooldown (stretch, added if time allows)
"""
Tiered abuse/toxicity moderation for claim chats.

This ships as a small keyword-based heuristic so the pipeline (and the
tiered response contract the frontend expects) works out of the box with
zero external dependencies. Swap `_classify` for a real moderation model or
API call — the three-tier contract below is the part that matters:

  "ok"       -> message passes through
  "nudge"    -> mild hostility: message is delivered + an in-app nudge to stay respectful
  "held"     -> clear abuse: message is held back, sender warned, repeat offenses flagged
  "frozen"   -> severe/threatening: conversation frozen, routed to human/admin review
"""

_MILD = {"stupid", "idiot", "dumb", "shut up", "annoying"}
_CLEAR = {"scam", "liar", "fraud", "thief", "cheat"}
_SEVERE = {"kill", "hurt you", "come find you", "threat", "attack you"}


def classify_message(text: str) -> str:
    lowered = (text or "").lower()

    if any(term in lowered for term in _SEVERE):
        return "frozen"
    if any(term in lowered for term in _CLEAR):
        return "held"
    if any(term in lowered for term in _MILD):
        return "nudge"
    return "ok"


TIER_RESPONSES = {
    "ok": "",
    "nudge": "Let's keep this respectful — please stay courteous while verifying ownership.",
    "held": "Your message was held for review due to its tone. Repeated issues will be flagged to admins.",
    "frozen": "This conversation has been frozen and routed to admin review due to concerning language.",
}

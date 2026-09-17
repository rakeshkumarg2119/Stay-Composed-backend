from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Lost & Found items
# ---------------------------------------------------------------------------

class ItemCreate(BaseModel):
    type: Literal["lost", "found"]
    title: str = Field(min_length=2, max_length=140)
    category: str = "General"
    location: Optional[str] = None
    locationDetail: Optional[str] = None  # only meaningful when location == "Others"
    date: Optional[str] = None
    description: str = Field(min_length=2, max_length=1000)
    imageUrl: Optional[str] = None  # already uploaded to Cloudinary by the frontend

    # Lost-only: identifying details ONLY the true owner would know (hashed at rest)
    secretFeatures: Optional[list[str]] = None

    # Found-only: paired challenge questions + the finder's hidden secret answers
    challengeQuestions: Optional[list[str]] = None
    secretAnswers: Optional[list[str]] = None

    reporterEmail: EmailStr
    reporterName: Optional[str] = None


class ItemOut(BaseModel):
    id: str
    type: Literal["lost", "found"]
    title: str
    category: str
    location: Optional[str] = None
    date: Optional[str] = None
    description: str
    imageUrl: Optional[str] = None
    secretFeatures: Optional[list[str]] = None  # Returned only to the reporter on /mine
    challengeQuestions: Optional[list[str]] = None  # safe to expose (questions only, not answers)
    reportedBy: str
    status: Literal["open", "matched", "verified", "handed_over", "resolved", "expired"]
    createdAt: datetime


class CandidateMatch(BaseModel):
    candidate: ItemOut
    forComplaintId: str
    confidence: int


class FounderCandidateMatch(BaseModel):
    """
    The founder-side mirror of CandidateMatch: a lost complaint (candidate)
    that matches one of the current user's *found* items. Without this,
    /items/mine only ever surfaced matches for the current user's lost
    complaints, so a founder never saw an in-app "someone lost something
    matching your found item" notice — only the backend's separate email
    ever told them (see items.py::_notify_new_matches, which is the only
    place both directions were previously covered).
    """
    candidate: ItemOut
    forFoundItemId: str
    confidence: int


class MineResponse(BaseModel):
    myComplaints: list[ItemOut]
    myFoundItems: list[ItemOut]
    candidateMatches: list[CandidateMatch]
    founderMatches: list[FounderCandidateMatch] = []
    chatConfidenceThreshold: int


# ---------------------------------------------------------------------------
# Claim / verification
# ---------------------------------------------------------------------------

class ClaimRequest(BaseModel):
    complaintId: str  # the claimant's own lost-item complaint id
    foundItemId: str  # the found item being claimed
    answers: list[str]  # claimant's answers, aligned by index to the found item's challengeQuestions
    claimantEmail: EmailStr


class ClaimResult(BaseModel):
    verified: bool
    matchedFields: int
    totalFields: int
    message: str
    cooldownUntil: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Blood alert
# ---------------------------------------------------------------------------

class BloodAlertRequest(BaseModel):
    studentName: str = Field(min_length=2, max_length=120)
    bloodType: str = Field(min_length=1, max_length=60)
    phoneNumber: str = Field(min_length=6, max_length=20)
    senderEmail: EmailStr


class BloodAlertResult(BaseModel):
    success: bool
    recipientsNotified: int
    message: str


# ---------------------------------------------------------------------------
# Chat — only unlocked between a claimant and founder once the AI match
# confidence crosses chat_min_confidence ("exact requirement" match). Free
# text both ways, but closes permanently once the founder starts verification.
# ---------------------------------------------------------------------------

class ChatTemplatesOut(BaseModel):
    role: str
    questions: list[str]
    answers: list[str]
    freeTextAllowed: bool

class ChatThreadOut(BaseModel):
    threadId: str
    complaintId: str
    foundItemId: str
    claimantEmail: EmailStr
    founderEmail: EmailStr
    claimantName: Optional[str] = None
    founderName: Optional[str] = None
    confidence: int
    status: Literal["chat", "verifying", "verified", "handed_over", "resolved", "closed", "frozen"]
    createdAt: datetime
    verificationStartedAt: Optional[datetime] = None
    handedOverAt: Optional[datetime] = None
    heldMessageCount: int = 0


class ChatThreadRequest(BaseModel):
    complaintId: str
    foundItemId: str
    requesterEmail: EmailStr


class ChatMessageIn(BaseModel):
    senderEmail: EmailStr
    text: str = Field(min_length=1, max_length=1000)


class ChatMessageOut(BaseModel):
    id: str
    threadId: str
    senderEmail: EmailStr
    text: str
    sentAt: datetime


# ---------------------------------------------------------------------------
# Push notification device tokens
# ---------------------------------------------------------------------------

class DeviceTokenRegister(BaseModel):
    email: EmailStr
    token: str = Field(min_length=10)
    platform: Literal["android", "ios"] = "android"


# ---------------------------------------------------------------------------
# In-app notifications feed. `type` is snake_case on the wire — the Flutter
# side's NotificationsController._parseType() converts snake_case ->
# camelCase to match the NotificationType enum, so every producer (claims.py
# etc.) must emit snake_case here too.
# ---------------------------------------------------------------------------

class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    body: str
    createdAt: datetime
    isRead: bool = False
    relatedId: Optional[str] = None
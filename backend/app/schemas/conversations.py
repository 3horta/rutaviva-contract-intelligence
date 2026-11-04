"""API models for conversations and the streamed answer. The frontend types mirror these."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.query import AnswerDetails, AnswerStatusOut, Citation, Confidence

MessageRoleOut = Literal["user", "assistant"]


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    # Last activity (a saved turn); the listing is ordered by it, newest first.
    updated_at: datetime
    message_count: int


class ConversationList(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[ConversationSummary]


class MessageOut(BaseModel):
    """A stored message. `status`, `confidence` and `details` are null for user messages
    (and `citations` / `warnings` empty)."""

    id: uuid.UUID
    role: MessageRoleOut
    content: str
    status: AnswerStatusOut | None
    confidence: Confidence | None
    citations: list[Citation]
    warnings: list[str]
    details: AnswerDetails | None
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]  # in conversation order


class ConversationRename(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


# --- Server-Sent Events of POST /query/stream -------------------------------------------

Phase = Literal["contextualizing", "searching", "drafting", "validating"]

PHASE_LABELS: dict[str, str] = {
    "contextualizing": "Understanding your follow-up",
    "searching": "Searching your documents",
    "drafting": "Drafting the answer",
    "validating": "Checking the citations",
}


class ConversationEvent(BaseModel):
    """First event of every stream. For a new conversation the id is reserved here and
    the conversation is created with its first saved turn."""

    conversation_id: uuid.UUID
    title: str


class PhaseEvent(BaseModel):
    phase: Phase
    label: str

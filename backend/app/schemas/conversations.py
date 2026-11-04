"""API models for conversations and the streamed answer. The frontend types mirror these."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.db.models import FEEDBACK_COMMENT_MAX_CHARS
from app.schemas.query import AnswerDetails, Citation, Confidence

MessageRoleOut = Literal["user", "assistant"]
# A stored answer's status: the answer statuses, plus `interrupted` (the client
# disconnected before the answer was saved: the question was kept, the answer is empty).
MessageStatusOut = Literal["answered", "insufficient_evidence", "interrupted"]


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


FeedbackRatingOut = Literal["up", "down"]


class MessageFeedbackOut(BaseModel):
    """One rater's rating of an answer. `comment` is trimmed, null when there is none."""

    rating: FeedbackRatingOut
    comment: str | None


class FeedbackTotals(BaseModel):
    """How many raters of the tenant found an answer useful (`up`) or not (`down`)."""

    up: int = 0
    down: int = 0


class FeedbackResult(BaseModel):
    """After a PUT or DELETE: this rater's feedback (null once cleared) and the totals."""

    feedback: MessageFeedbackOut | None
    totals: FeedbackTotals


class MessageFeedbackIn(BaseModel):
    """Body of `PUT .../feedback`. A blank comment is stored as none."""

    model_config = {"extra": "forbid"}

    rating: FeedbackRatingOut
    comment: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, max_length=FEEDBACK_COMMENT_MAX_CHARS)
        ]
        | None
    ) = None


class MessageOut(BaseModel):
    """A stored message. `status`, `confidence` and `details` are null for user messages
    (and `citations` / `warnings` / `suggestions` empty). An `interrupted` answer has
    empty `content`, null `confidence` and `details`."""

    id: uuid.UUID
    role: MessageRoleOut
    content: str
    status: MessageStatusOut | None
    confidence: Confidence | None
    citations: list[Citation]
    warnings: list[str]
    details: AnswerDetails | None
    # Follow-up questions suggested after an `answered` turn (real LLM only; may be
    # empty, and arrive a moment after the answer).
    suggestions: list[str] = Field(default_factory=list)
    # The rating of the rater named by `X-Rater-Id` on the request; null when that rater
    # has not rated it, without the header, and on user messages.
    feedback: MessageFeedbackOut | None = None
    # Ratings of all the tenant's raters; null on user messages.
    feedback_totals: FeedbackTotals | None = None
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


class SuggestionsEvent(BaseModel):
    """After `result`, only when there are suggestions: follow-up questions for the
    answer `message_id`, already stored with it."""

    message_id: uuid.UUID
    suggestions: list[str]


class TitleEvent(BaseModel):
    """After `result`, only for a new conversation whose title the LLM wrote (already
    stored)."""

    conversation_id: uuid.UUID
    title: str

"""Conversation endpoints: list, read, rename and delete, and feedback on an answer.
Turns are added by `/query`.

A conversation of another tenant answers 404, never 403, so its existence is not revealed.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.api.deps import CurrentTenant, TenantSession
from app.conversations import feedback, service
from app.schemas.conversations import (
    ConversationList,
    ConversationOut,
    ConversationRename,
    ConversationSummary,
    FeedbackResult,
    MessageFeedbackIn,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# An anonymous id per browser, to tell raters apart (ADR-019). Not authentication: the
# tenant still comes from the JWT only.
RATER_HEADER = "X-Rater-Id"
RaterId = Annotated[
    uuid.UUID,
    Header(alias=RATER_HEADER, description="Anonymous id of the rater (a UUID the client keeps)."),
]
OptionalRaterId = Annotated[
    uuid.UUID | None,
    Header(alias=RATER_HEADER, description="Include this rater's own feedback on each answer."),
]


@router.get("", response_model=ConversationList)
def list_conversations(
    tenant: CurrentTenant,
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> ConversationList:
    """The tenant's conversations, most recent activity first. `search` matches the title
    (case-insensitive)."""
    return service.list_conversations(session, tenant.id, limit=limit, offset=offset, search=search)


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: uuid.UUID,
    tenant: CurrentTenant,
    session: TenantSession,
    rater_id: OptionalRaterId = None,
) -> ConversationOut:
    """The conversation with all its messages, in order. Each answer carries the feedback
    totals, and the caller's own feedback when `X-Rater-Id` is sent."""
    return service.get_conversation(session, tenant.id, conversation_id, rater_id)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationRename,
    tenant: CurrentTenant,
    session: TenantSession,
) -> ConversationSummary:
    return service.rename_conversation(session, tenant.id, conversation_id, body.title)


@router.delete("/{conversation_id}", status_code=204, response_class=Response)
def delete_conversation(
    conversation_id: uuid.UUID, tenant: CurrentTenant, session: TenantSession
) -> Response:
    """Delete the conversation and all its messages."""
    service.delete_conversation(session, tenant.id, conversation_id)
    return Response(status_code=204)


@router.put("/{conversation_id}/messages/{message_id}/feedback", response_model=FeedbackResult)
def rate_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageFeedbackIn,
    rater_id: RaterId,
    tenant: CurrentTenant,
    session: TenantSession,
) -> FeedbackResult:
    """Rate an answer (`up` / `down`, optional comment), replacing this rater's earlier
    rating. Returns it with the answer's totals. 404 for a message not in this tenant's
    conversation; 422 for a question, an interrupted turn, an invalid body or a missing or
    invalid `X-Rater-Id`."""
    return feedback.rate_message(session, tenant.id, conversation_id, message_id, rater_id, body)


@router.delete("/{conversation_id}/messages/{message_id}/feedback", response_model=FeedbackResult)
def clear_feedback(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    rater_id: RaterId,
    tenant: CurrentTenant,
    session: TenantSession,
) -> FeedbackResult:
    """Remove this rater's rating of an answer (also when there was none); other raters'
    stay. Returns the answer's totals."""
    return feedback.clear_feedback(session, tenant.id, conversation_id, message_id, rater_id)

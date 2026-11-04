"""Feedback on answers: a user's rating (`up` / `down`) and an optional comment.

One per assistant message, replaced when rated again (an upsert), cleared with DELETE,
and deleted with the message. Only stored answers can be rated: a user message or an
interrupted turn (which has no answer) is a 422. A message of another tenant, or one
that is not in that conversation, is a 404, like a missing one.

The comment is the user's text: it is stored, never logged nor put on a span.
"""

import logging
import uuid

from opentelemetry import trace
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import NotFound, Unprocessable
from app.db.models import AnswerStatus, Message, MessageFeedback, MessageRole
from app.schemas.conversations import MessageFeedbackIn, MessageFeedbackOut

logger = logging.getLogger(__name__)

# Span attribute: the rating given (or "cleared").
FEEDBACK_RATING = "feedback.rating"
_NOT_FOUND = "Message not found."


def rate_message(
    session: Session,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageFeedbackIn,
) -> MessageFeedbackOut:
    """Store the rating of an answer, replacing any earlier one."""
    _rateable_answer(session, tenant_id, conversation_id, message_id)
    comment = body.comment or None  # blank (after trimming) is no comment
    values = {"rating": body.rating, "comment": comment}
    session.execute(
        insert(MessageFeedback)
        .values(message_id=message_id, tenant_id=tenant_id, **values)
        .on_conflict_do_update(
            index_elements=[MessageFeedback.message_id],
            set_={**values, "updated_at": func.now()},
        )
    )
    _record(tenant_id, conversation_id, message_id, body.rating, has_comment=comment is not None)
    return MessageFeedbackOut(rating=body.rating, comment=comment)


def clear_feedback(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> None:
    """Remove the rating of an answer (nothing to remove is not an error)."""
    _rateable_answer(session, tenant_id, conversation_id, message_id)
    session.execute(
        delete(MessageFeedback).where(
            MessageFeedback.message_id == message_id, MessageFeedback.tenant_id == tenant_id
        )
    )
    _record(tenant_id, conversation_id, message_id, "cleared", has_comment=False)


def feedback_by_message(
    session: Session, tenant_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, MessageFeedbackOut]:
    """The feedback of the given messages, for the conversation's listing."""
    if not message_ids:
        return {}
    rows = session.scalars(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id, MessageFeedback.message_id.in_(message_ids)
        )
    ).all()
    return {
        row.message_id: MessageFeedbackOut(rating=row.rating, comment=row.comment) for row in rows
    }


def _rateable_answer(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID
) -> Message:
    message = session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.tenant_id == tenant_id,
        )
    )
    if message is None:
        raise NotFound(_NOT_FOUND)
    if message.role != MessageRole.ASSISTANT:
        raise Unprocessable("Only an answer can be rated, not a question.")
    if message.status == AnswerStatus.INTERRUPTED:
        raise Unprocessable("An interrupted turn has no answer to rate.")
    return message


def _record(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    rating: str,
    *,
    has_comment: bool,
) -> None:
    # On the request's span; never the comment itself.
    trace.get_current_span().set_attribute(FEEDBACK_RATING, rating)
    logger.info(
        "feedback.recorded",
        extra={
            "tenant_id": str(tenant_id),
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "rating": rating,
            "has_comment": has_comment,
        },
    )

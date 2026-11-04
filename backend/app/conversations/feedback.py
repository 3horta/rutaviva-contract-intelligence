"""Feedback on answers: each rater's rating (`up` / `down`) and optional comment.

A rater is an anonymous id the browser makes up and sends in `X-Rater-Id`. It tells two
people of the same tenant apart; it authenticates no one (ADR-019). One rating per answer
and rater, replaced when that rater rates again (an upsert) and cleared with DELETE (only
theirs); all of them are deleted with the message. The conversation returns the caller's
own rating plus the totals of all the tenant's raters.

Only stored answers can be rated: a user message or an interrupted turn (which has no
answer) is a 422. A message of another tenant, or one that is not in that conversation,
is a 404, like a missing one.

The comment is the user's text and the rater id is a per-browser identifier: neither is
logged nor put on a span.
"""

import logging
import uuid

from opentelemetry import trace
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import NotFound, Unprocessable
from app.db.models import AnswerStatus, FeedbackRating, Message, MessageFeedback, MessageRole
from app.schemas.conversations import (
    FeedbackResult,
    FeedbackTotals,
    MessageFeedbackIn,
    MessageFeedbackOut,
)

logger = logging.getLogger(__name__)

# Span attribute: the rating given (or "cleared").
FEEDBACK_RATING = "feedback.rating"
_NOT_FOUND = "Message not found."


def rate_message(
    session: Session,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    rater_id: uuid.UUID,
    body: MessageFeedbackIn,
) -> FeedbackResult:
    """Store this rater's rating of an answer, replacing their earlier one."""
    _rateable_answer(session, tenant_id, conversation_id, message_id)
    comment = body.comment or None  # blank (after trimming) is no comment
    values = {"rating": body.rating, "comment": comment}
    session.execute(
        insert(MessageFeedback)
        .values(message_id=message_id, rater_id=rater_id, tenant_id=tenant_id, **values)
        .on_conflict_do_update(
            index_elements=[MessageFeedback.message_id, MessageFeedback.rater_id],
            set_={**values, "updated_at": func.now()},
        )
    )
    _record(message_id, body.rating, has_comment=comment is not None)
    return FeedbackResult(
        feedback=MessageFeedbackOut(rating=body.rating, comment=comment),
        totals=totals_by_message(session, tenant_id, [message_id]).get(
            message_id, FeedbackTotals()
        ),
    )


def clear_feedback(
    session: Session,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    rater_id: uuid.UUID,
) -> FeedbackResult:
    """Remove this rater's rating of an answer (nothing to remove is not an error);
    other raters' stay."""
    _rateable_answer(session, tenant_id, conversation_id, message_id)
    session.execute(
        delete(MessageFeedback).where(
            MessageFeedback.message_id == message_id,
            MessageFeedback.rater_id == rater_id,
            MessageFeedback.tenant_id == tenant_id,
        )
    )
    _record(message_id, "cleared", has_comment=False)
    return FeedbackResult(
        feedback=None,
        totals=totals_by_message(session, tenant_id, [message_id]).get(
            message_id, FeedbackTotals()
        ),
    )


def own_feedback(
    session: Session, tenant_id: uuid.UUID, message_ids: list[uuid.UUID], rater_id: uuid.UUID
) -> dict[uuid.UUID, MessageFeedbackOut]:
    """This rater's feedback on the given messages."""
    if not message_ids:
        return {}
    rows = session.scalars(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.rater_id == rater_id,
            MessageFeedback.message_id.in_(message_ids),
        )
    ).all()
    return {
        row.message_id: MessageFeedbackOut(rating=row.rating, comment=row.comment) for row in rows
    }


def totals_by_message(
    session: Session, tenant_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, FeedbackTotals]:
    """Up and down counts of all the tenant's raters, for the messages that have any."""
    if not message_ids:
        return {}
    rows = session.execute(
        select(
            MessageFeedback.message_id,
            func.count().filter(MessageFeedback.rating == FeedbackRating.UP),
            func.count().filter(MessageFeedback.rating == FeedbackRating.DOWN),
        )
        .where(MessageFeedback.tenant_id == tenant_id, MessageFeedback.message_id.in_(message_ids))
        .group_by(MessageFeedback.message_id)
    ).all()
    return {message_id: FeedbackTotals(up=up, down=down) for message_id, up, down in rows}


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


def _record(message_id: uuid.UUID, rating: str, *, has_comment: bool) -> None:
    # On the request's span; never the comment nor the rater id.
    trace.get_current_span().set_attribute(FEEDBACK_RATING, rating)
    logger.info(
        "feedback.recorded",
        extra={"message_id": str(message_id), "rating": rating, "has_comment": has_comment},
    )

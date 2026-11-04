"""Conversations: turns (question + answer) stored together, and their CRUD.

A turn is written atomically AFTER a successful answer (`answered` or
`insufficient_evidence`): the user message, the assistant message and the conversation's
`updated_at` in one transaction. If the answer fails (503/504, unexpected error) nothing
is stored, not even the question. A new conversation is created with its first turn.

Follow-ups: before retrieval the question is resolved against the last
`MAX_CONTEXT_TURNS` turns (see `resolver.py`), so "and what is the cap?" is searched
together with the question it continues. The user message keeps the text as typed; the
assistant message stores `standalone_question` only when the context was used.

Retry: `retry=true` asks the LAST question again and replaces its turn. It needs a
conversation with at least one turn and the same question text (otherwise 422: the
client asked to retry something that is not the last question). The old turn is deleted
in the same transaction that saves the new one, so a failed retry keeps the old answer.

Every query filters by `tenant_id` explicitly, with RLS underneath (ADR-002). A
conversation of another tenant is a 404, never a 403.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.conversations.resolver import (
    MAX_CONTEXT_TURNS,
    QuestionResolver,
    ResolvedQuestion,
    Turn,
    unchanged,
)
from app.core.config import Settings
from app.core.errors import NotFound, Unprocessable
from app.core.tracing import TENANT_ID, span
from app.db.models import Conversation, Message, MessageRole
from app.generation.service import AnswerOutcome, PhaseCallback, answer_with_usage
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import LLMProvider
from app.schemas.conversations import (
    ConversationList,
    ConversationOut,
    ConversationSummary,
    MessageOut,
)
from app.schemas.query import AnswerDetails, QueryResponse

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 80
_NOT_FOUND = "Conversation not found."


def make_title(question: str) -> str:
    """The first question on one line, cut to `TITLE_MAX_CHARS`."""
    return " ".join(question.split())[:TITLE_MAX_CHARS]


# --- Turns --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnPlan:
    """What a new turn needs, decided (and checked) before answering."""

    conversation_id: uuid.UUID  # for a new conversation, an id reserved for it
    title: str
    is_new: bool
    context: list[Turn]  # earlier turns for the resolver, oldest first
    # Retry: the (user, assistant) message ids of the turn being replaced.
    replaces: tuple[uuid.UUID, uuid.UUID] | None = None


@dataclass(frozen=True)
class _StoredTurn:
    user: Message
    assistant: Message


def plan_turn(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    conversation_id: uuid.UUID | None,
    question: str,
    retry: bool,
) -> TurnPlan:
    """Check the request against the stored conversation. Raises `NotFound` (missing or
    another tenant's conversation) and `Unprocessable` (a retry that does not repeat the
    last question). Runs BEFORE answering, so the stream fails before it starts."""
    if conversation_id is None:
        if retry:
            raise Unprocessable("A retry needs the conversation_id of the turn to retry.")
        return TurnPlan(
            conversation_id=uuid.uuid4(), title=make_title(question), is_new=True, context=[]
        )

    conversation = _get_or_404(session, tenant_id, conversation_id)
    turns = _last_turns(session, tenant_id, conversation_id, MAX_CONTEXT_TURNS + 1)
    replaces = None
    if retry:
        if not turns:
            raise Unprocessable("The conversation has no turn to retry.")
        last = turns.pop()
        if last.user.content != question:
            raise Unprocessable("A retry must repeat the last question of the conversation.")
        replaces = (last.user.id, last.assistant.id)
    return TurnPlan(
        conversation_id=conversation.id,
        title=conversation.title,
        is_new=False,
        context=[_as_context(turn) for turn in turns[-MAX_CONTEXT_TURNS:]],
        replaces=replaces,
    )


def run_turn(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    plan: TurnPlan,
    question: str,
    resolver: QuestionResolver,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
    on_phase: PhaseCallback | None = None,
) -> QueryResponse:
    """Resolve the question, answer it and store the turn (in `session`'s transaction:
    the caller commits). Provider errors propagate and nothing is stored."""
    with span("conversation.turn", **{TENANT_ID: str(tenant_id)}):
        if plan.context:
            if on_phase:
                on_phase("contextualizing")
            resolved = resolver.resolve(question, plan.context)
        else:
            resolved = unchanged(question)

        outcome = answer_with_usage(
            resolved.standalone,
            tenant_id=tenant_id,
            session=session,
            embeddings=embeddings,
            llm=llm,
            settings=settings,
            on_phase=on_phase,
        )
        return _save_turn(session, tenant_id, plan, resolved, outcome)


def _save_turn(
    session: Session,
    tenant_id: uuid.UUID,
    plan: TurnPlan,
    resolved: ResolvedQuestion,
    outcome: AnswerOutcome,
) -> QueryResponse:
    response = outcome.response
    assert response.details is not None  # the answer service always fills them
    details = response.details.model_copy(
        update={
            "standalone_question": resolved.standalone if resolved.uses_context else None,
            "uses_context": resolved.uses_context,
        }
    )

    if plan.is_new:
        session.add(Conversation(id=plan.conversation_id, tenant_id=tenant_id, title=plan.title))
        session.flush()
        logger.info(
            "conversation.created",
            extra={"tenant_id": str(tenant_id), "conversation_id": str(plan.conversation_id)},
        )
    else:
        # Row lock: two turns saved at the same time in this conversation take
        # consecutive sequence numbers instead of colliding on the unique constraint.
        locked = session.scalar(
            select(Conversation.id)
            .where(Conversation.id == plan.conversation_id, Conversation.tenant_id == tenant_id)
            .with_for_update()
        )
        if locked is None:  # deleted while the answer was being generated
            raise NotFound(_NOT_FOUND)
        if plan.replaces:
            # By id: if a concurrent retry already removed them, this deletes nothing.
            session.execute(
                delete(Message).where(Message.id.in_(plan.replaces), Message.tenant_id == tenant_id)
            )
        session.execute(
            update(Conversation)
            .where(Conversation.id == plan.conversation_id, Conversation.tenant_id == tenant_id)
            .values(updated_at=func.now())
        )

    last_sequence = session.scalar(
        select(func.max(Message.sequence)).where(
            Message.conversation_id == plan.conversation_id, Message.tenant_id == tenant_id
        )
    )
    sequence = (last_sequence or 0) + 1
    assistant_id = uuid.uuid4()
    session.add_all(
        [
            Message(
                conversation_id=plan.conversation_id,
                tenant_id=tenant_id,
                sequence=sequence,
                role=MessageRole.USER,
                content=resolved.asked,  # as typed
            ),
            Message(
                id=assistant_id,
                conversation_id=plan.conversation_id,
                tenant_id=tenant_id,
                sequence=sequence + 1,
                role=MessageRole.ASSISTANT,
                content=response.answer,
                standalone_question=details.standalone_question,
                status=response.status,
                confidence=response.confidence,
                citations=[c.model_dump(mode="json") for c in response.citations],
                warnings=list(response.warnings),
                details=details.model_dump(mode="json"),
                latency_ms=details.latency_ms,
                llm_model=details.llm_model,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                trace_id=details.trace_id,
            ),
        ]
    )
    session.flush()
    logger.info(
        "conversation.turn_saved",
        extra={
            "tenant_id": str(tenant_id),
            "conversation_id": str(plan.conversation_id),
            "message_id": str(assistant_id),
            "sequence": sequence,
            "retry": plan.replaces is not None,
            "uses_context": resolved.uses_context,
        },
    )
    return response.model_copy(
        update={
            "conversation_id": plan.conversation_id,
            "message_id": assistant_id,
            "details": details,
        }
    )


def _last_turns(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, count: int
) -> list[_StoredTurn]:
    """The last `count` turns, oldest first."""
    newest_first = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.tenant_id == tenant_id)
        .order_by(Message.sequence.desc())
        .limit(count * 2)
    ).all()
    messages = list(reversed(newest_first))
    # Turns are saved as (user, assistant) pairs; skip a leading assistant message cut
    # off by the limit.
    turns = []
    for user, assistant in zip(messages, messages[1:], strict=False):
        if user.role == MessageRole.USER and assistant.role == MessageRole.ASSISTANT:
            turns.append(_StoredTurn(user=user, assistant=assistant))
    return turns


def _as_context(turn: _StoredTurn) -> Turn:
    # The standalone question if the turn was itself a follow-up, so context carries over.
    question = turn.assistant.standalone_question or turn.user.content
    return Turn(question=question, answer=turn.assistant.content)


# --- CRUD ---------------------------------------------------------------------------------


def list_conversations(
    session: Session, tenant_id: uuid.UUID, *, limit: int, offset: int, search: str | None
) -> ConversationList:
    """Most recent activity first. `search` matches the title (case-insensitive, with
    `%` and `_` taken literally)."""
    filters = [Conversation.tenant_id == tenant_id]
    if search:
        filters.append(Conversation.title.ilike(f"%{escape_like(search)}%", escape="\\"))

    count = session.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0
    rows = session.execute(
        select(Conversation, _message_count())
        .where(*filters)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return ConversationList(
        count=count,
        limit=limit,
        offset=offset,
        results=[_summary(conversation, messages) for conversation, messages in rows],
    )


def get_conversation(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID
) -> ConversationOut:
    conversation = _get_or_404(session, tenant_id, conversation_id)
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.tenant_id == tenant_id)
        .order_by(Message.sequence)
    ).all()
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[_message_out(message) for message in messages],
    )


def rename_conversation(
    session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> ConversationSummary:
    # A rename is not activity: `updated_at` is kept (it would otherwise be bumped by the
    # column's `onupdate`), so the conversation does not jump to the top of the list.
    renamed = session.scalar(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
        .values(title=title, updated_at=Conversation.updated_at)
        .returning(Conversation.id)
    )
    if renamed is None:
        raise NotFound(_NOT_FOUND)
    conversation, messages = session.execute(
        select(Conversation, _message_count()).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
        )
    ).one()
    return _summary(conversation, messages)


def delete_conversation(session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    _get_or_404(session, tenant_id, conversation_id)
    messages = _count_messages(session, tenant_id, conversation_id)
    # Its messages go with it (ON DELETE CASCADE on the composite FK).
    session.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
        )
    )
    logger.info(
        "conversation.deleted",
        extra={
            "tenant_id": str(tenant_id),
            "conversation_id": str(conversation_id),
            "messages": messages,
        },
    )


# --- Helpers ------------------------------------------------------------------------------


def _get_or_404(session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.tenant_id == tenant_id
        )
    )
    if conversation is None:
        raise NotFound(_NOT_FOUND)
    return conversation


def _message_count():  # a correlated scalar subquery, one per listed conversation
    return (
        select(func.count(Message.id))
        .where(
            Message.conversation_id == Conversation.id,
            Message.tenant_id == Conversation.tenant_id,
        )
        .correlate(Conversation)
        .scalar_subquery()
    )


def _count_messages(session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id, Message.tenant_id == tenant_id
            )
        )
        or 0
    )


def _summary(conversation: Conversation, message_count: int) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=message_count,
    )


def _message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        status=message.status,
        confidence=message.confidence,
        citations=message.citations,
        warnings=message.warnings,
        details=AnswerDetails.model_validate(message.details) if message.details else None,
        created_at=message.created_at,
    )


def escape_like(text: str) -> str:
    """Make `%` and `_` in the user's search literal (with `\\` as the escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

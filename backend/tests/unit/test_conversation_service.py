"""Conversation service logic that needs no database: which turns are follow-up context,
and how a stored message is returned. (Storage itself: tests/integration.)"""

import uuid
from datetime import UTC, datetime

from app.conversations.resolver import MAX_CONTEXT_TURNS
from app.conversations.service import _message_out, _StoredTurn, context_from
from app.db.models import Message, MessageRole


def _turn(question: str, status: str, answer: str = "An answer.") -> _StoredTurn:
    user = Message(id=uuid.uuid4(), role=MessageRole.USER, content=question)
    assistant = Message(
        id=uuid.uuid4(),
        role=MessageRole.ASSISTANT,
        content="" if status == "interrupted" else answer,
        status=status,
    )
    return _StoredTurn(user=user, assistant=assistant)


def test_interrupted_turns_are_not_context() -> None:
    turns = [
        _turn("What is the penalty?", "answered"),
        _turn("Is there a cap?", "interrupted"),
        _turn("Who pays freight?", "insufficient_evidence"),
        _turn("And the notice period?", "interrupted"),
    ]
    assert [turn.question for turn in context_from(turns)] == [
        "What is the penalty?",
        "Who pays freight?",
    ]


def test_context_keeps_the_most_recent_answered_turns() -> None:
    turns = [_turn(f"Question {n}?", "answered") for n in range(MAX_CONTEXT_TURNS + 2)]
    turns.append(_turn("Stopped?", "interrupted"))
    context = context_from(turns)
    assert len(context) == MAX_CONTEXT_TURNS
    assert context[-1].question == f"Question {MAX_CONTEXT_TURNS + 1}?"


def test_only_interrupted_turns_give_no_context() -> None:
    assert context_from([_turn("Stopped?", "interrupted")]) == []


def _stored(details: dict | None, status: str = "answered") -> Message:
    return Message(
        id=uuid.uuid4(),
        role=MessageRole.ASSISTANT,
        content="An answer.",
        status=status,
        confidence="high" if status == "answered" else None,
        citations=[],
        warnings=[],
        details=details,
        created_at=datetime.now(UTC),
    )


DETAILS = {
    "retrieved": [],
    "vector_hits": 0,
    "full_text_hits": 0,
    "attempts": 1,
    "insufficient_reason": None,
    "latency_ms": 10,
    "llm_model": "fake",
    "trace_id": None,
}


def test_suggestions_are_read_from_the_details_but_not_part_of_them() -> None:
    out = _message_out(_stored({**DETAILS, "suggestions": ["Is there a cap?"]}))
    assert out.suggestions == ["Is there a cap?"]
    assert out.details is not None
    assert "suggestions" not in out.details.model_dump()


def test_a_message_without_suggestions_has_an_empty_list() -> None:
    assert _message_out(_stored(DETAILS)).suggestions == []


def test_an_interrupted_answer_has_no_details() -> None:
    out = _message_out(_stored(None, status="interrupted"))
    assert out.status == "interrupted"
    assert out.details is None and out.suggestions == [] and out.confidence is None

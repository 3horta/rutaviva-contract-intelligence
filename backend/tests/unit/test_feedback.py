"""Feedback on answers without a database: the request body, which messages can be
rated, the upsert issued, and what is logged and traced (never the comment)."""

import logging
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

from app.api.deps import TenantContext, get_current_tenant, get_tenant_session
from app.conversations import feedback
from app.core.errors import NotFound, Unprocessable
from app.db.models import Message
from app.main import create_app
from app.schemas.conversations import MessageFeedbackIn

TENANT = uuid.uuid4()
CONVERSATION = uuid.uuid4()
MESSAGE = uuid.uuid4()
SECRET_COMMENT = "The clause quoted is from the wrong annex."


class FakeSession:
    """Returns `message` for the lookup and records the statements executed."""

    def __init__(self, message: Message | None) -> None:
        self.message = message
        self.executed: list[Any] = []

    def scalar(self, _statement: Any) -> Message | None:
        return self.message

    def execute(self, statement: Any) -> None:
        self.executed.append(statement)


def _answer(status: str = "answered", role: str = "assistant") -> Message:
    return Message(
        id=MESSAGE,
        conversation_id=CONVERSATION,
        tenant_id=TENANT,
        sequence=2,
        role=role,
        content="",
        status=status if role == "assistant" else None,
    )


# --- Request body ------------------------------------------------------------------------


def test_comment_is_trimmed_and_optional() -> None:
    assert MessageFeedbackIn(rating="up").comment is None
    assert MessageFeedbackIn(rating="down", comment="  wrong page  ").comment == "wrong page"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"rating": "meh"},
        {"rating": True},
        {"rating": "up", "comment": "x" * 1001},
        {"rating": "up", "helpful": True},  # unknown fields are refused
    ],
)
def test_invalid_bodies_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MessageFeedbackIn.model_validate(body)


def test_a_comment_of_1000_characters_is_accepted() -> None:
    assert MessageFeedbackIn(rating="up", comment="x" * 1000).comment == "x" * 1000


# --- Which messages can be rated ---------------------------------------------------------


def test_a_missing_message_is_not_found() -> None:
    with pytest.raises(NotFound):
        feedback.rate_message(
            FakeSession(None), TENANT, CONVERSATION, MESSAGE, MessageFeedbackIn(rating="up")
        )
    with pytest.raises(NotFound):
        feedback.clear_feedback(FakeSession(None), TENANT, CONVERSATION, MESSAGE)


@pytest.mark.parametrize("message", [_answer(role="user"), _answer(status="interrupted")])
def test_questions_and_interrupted_turns_cannot_be_rated(message: Message) -> None:
    session = FakeSession(message)
    with pytest.raises(Unprocessable):
        feedback.rate_message(
            session, TENANT, CONVERSATION, MESSAGE, MessageFeedbackIn(rating="up")
        )
    assert session.executed == []


@pytest.mark.parametrize("status", ["answered", "insufficient_evidence"])
def test_an_answer_is_rated_with_an_upsert(status: str) -> None:
    session = FakeSession(_answer(status))
    stored = feedback.rate_message(
        session, TENANT, CONVERSATION, MESSAGE, MessageFeedbackIn(rating="down", comment="  ")
    )
    assert stored.rating == "down"
    assert stored.comment is None  # blank after trimming: no comment
    [statement] = session.executed
    sql = str(statement.compile())
    assert "INSERT INTO message_feedback" in sql
    assert "ON CONFLICT (message_id) DO UPDATE" in sql


# --- Logs and spans ----------------------------------------------------------------------


def test_logs_and_traces_the_rating_but_never_the_comment(caplog) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with (
        caplog.at_level(logging.INFO, logger="app.conversations.feedback"),
        provider.get_tracer("test").start_as_current_span("PUT feedback"),
    ):
        feedback.rate_message(
            FakeSession(_answer()),
            TENANT,
            CONVERSATION,
            MESSAGE,
            MessageFeedbackIn(rating="down", comment=SECRET_COMMENT),
        )
        feedback.clear_feedback(FakeSession(_answer()), TENANT, CONVERSATION, MESSAGE)

    records = [r for r in caplog.records if r.getMessage() == "feedback.recorded"]
    assert [(r.rating, r.message_id, r.has_comment) for r in records] == [
        ("down", str(MESSAGE), True),
        ("cleared", str(MESSAGE), False),
    ]
    for record in records:
        assert SECRET_COMMENT not in str(record.__dict__)

    [span] = exporter.get_finished_spans()
    assert span.attributes[feedback.FEEDBACK_RATING] == "cleared"  # the last one wins
    assert SECRET_COMMENT not in str(dict(span.attributes))


# --- Routes ------------------------------------------------------------------------------


@pytest.fixture
def client(make_settings) -> tuple[TestClient, FakeSession]:
    session = FakeSession(_answer())
    app = create_app(make_settings())
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(id=TENANT, name="t")
    app.dependency_overrides[get_tenant_session] = lambda: session
    return TestClient(app), session


def _url(message_id: uuid.UUID = MESSAGE) -> str:
    return f"/conversations/{CONVERSATION}/messages/{message_id}/feedback"


def test_put_returns_the_stored_feedback(client) -> None:
    http, _ = client
    response = http.put(_url(), json={"rating": "up", "comment": " Exact clause. "})
    assert response.status_code == 200
    assert response.json() == {"rating": "up", "comment": "Exact clause."}


def test_delete_answers_204(client) -> None:
    http, _ = client
    assert http.delete(_url()).status_code == 204


def test_invalid_body_is_a_422_problem(client) -> None:
    http, session = client
    response = http.put(_url(), json={"rating": "sideways"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert session.executed == []


def test_a_question_is_a_422_and_an_unknown_message_a_404(client) -> None:
    http, session = client
    session.message = _answer(role="user")
    assert http.put(_url(), json={"rating": "up"}).status_code == 422
    session.message = None
    response = http.put(_url(uuid.uuid4()), json={"rating": "up"})
    assert response.status_code == 404
    assert response.json()["title"] == "Not found"

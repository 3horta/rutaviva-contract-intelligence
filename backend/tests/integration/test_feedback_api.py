"""Feedback on answers end to end: upsert, clear, the rating in the conversation, which
messages can be rated, tenant isolation through the API and in the database (RLS and the
composite FK), and feedback going away with its message. Real Postgres, fake providers,
the same test clients as test_conversations_api.py."""

import uuid

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import MessageFeedback
from app.db.session import tenant_transaction
from tests.integration.test_conversations_api import (  # noqa: F401  (the fixture)
    INCIDENT_QUESTION,
    PM_QUESTION,
    ask,
    setup,
)


def _url(conversation_id, message_id) -> str:
    return f"/conversations/{conversation_id}/messages/{message_id}/feedback"


def _feedback_rows(sessions: sessionmaker[Session], tenant_id: uuid.UUID) -> list[MessageFeedback]:
    with tenant_transaction(sessions, tenant_id) as session:
        return session.scalars(select(MessageFeedback)).all()


def _messages(client, conversation_id) -> list[dict]:
    return client.get(f"/conversations/{conversation_id}").json()["messages"]


def test_rating_is_upserted_and_shown_in_the_conversation(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])

    first = client.put(url, json={"rating": "up"})
    assert first.status_code == 200, first.text
    assert first.json() == {"rating": "up", "comment": None}
    [question, answer] = _messages(client, turn["conversation_id"])
    assert answer["feedback"] == {"rating": "up", "comment": None}
    assert question["feedback"] is None

    [before] = _feedback_rows(app_sessions, tenant_a)
    second = client.put(url, json={"rating": "down", "comment": "  Wrong annex.  "})
    assert second.json() == {"rating": "down", "comment": "Wrong annex."}
    [after] = _feedback_rows(app_sessions, tenant_a)  # still one row: replaced
    assert (after.rating, after.comment) == ("down", "Wrong annex.")
    assert after.created_at == before.created_at
    assert after.updated_at > before.updated_at
    assert _messages(client, turn["conversation_id"])[1]["feedback"]["rating"] == "down"


def test_rating_is_cleared(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])
    client.put(url, json={"rating": "down", "comment": "No citation for the interval."})

    assert client.delete(url).status_code == 204
    assert _feedback_rows(app_sessions, tenant_a) == []
    assert _messages(client, turn["conversation_id"])[1]["feedback"] is None
    assert client.delete(url).status_code == 204  # nothing left to clear: still fine


def test_only_answers_of_that_conversation_can_be_rated(setup) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    other = ask(client, INCIDENT_QUESTION)  # another conversation of the same tenant
    question_id = _messages(client, first["conversation_id"])[0]["id"]

    question = client.put(_url(first["conversation_id"], question_id), json={"rating": "up"})
    assert question.status_code == 422
    assert question.headers["content-type"].startswith("application/problem+json")
    assert client.delete(_url(first["conversation_id"], question_id)).status_code == 422

    elsewhere = client.put(
        _url(other["conversation_id"], first["message_id"]), json={"rating": "up"}
    )
    assert elsewhere.status_code == 404
    unknown = client.put(_url(first["conversation_id"], uuid.uuid4()), json={"rating": "up"})
    assert unknown.status_code == 404
    assert unknown.json()["title"] == "Not found"

    invalid = client.put(
        _url(first["conversation_id"], first["message_id"]),
        json={"rating": "up", "comment": "x" * 1001},
    )
    assert invalid.status_code == 422


def test_another_tenant_cannot_rate_or_see_the_rating(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, tenant_b, *_ = setup
    client_a, client_b = make(tenant_a), make(tenant_b)
    turn_a = ask(client_a, PM_QUESTION)
    turn_b = ask(client_b, INCIDENT_QUESTION)
    url_a = _url(turn_a["conversation_id"], turn_a["message_id"])
    client_a.put(url_a, json={"rating": "up", "comment": "Exact clause."})

    assert client_b.put(url_a, json={"rating": "down"}).status_code == 404
    assert client_b.delete(url_a).status_code == 404
    # B's own conversation with A's message id: still not found.
    mixed = _url(turn_b["conversation_id"], turn_a["message_id"])
    assert client_b.put(mixed, json={"rating": "down"}).status_code == 404

    [row] = _feedback_rows(app_sessions, tenant_a)
    assert (row.rating, row.comment) == ("up", "Exact clause.")
    assert _feedback_rows(app_sessions, tenant_b) == []


def test_rls_and_the_composite_fk_keep_feedback_in_its_tenant(
    setup,  # noqa: F811
    app_sessions: sessionmaker[Session],
    owner_engine: Engine,
) -> None:
    make, tenant_a, tenant_b, *_ = setup
    turn = ask(make(tenant_a), PM_QUESTION)
    message_id = uuid.UUID(turn["message_id"])
    make(tenant_a).put(_url(turn["conversation_id"], message_id), json={"rating": "up"})

    # No WHERE tenant_id at all: the policy hides A's row from B.
    with tenant_transaction(app_sessions, tenant_b) as session:
        assert session.scalar(select(func.count()).select_from(MessageFeedback)) == 0
    # Without a tenant, nothing is visible.
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(MessageFeedback)) == 0

    # The app role cannot write a row for another tenant...
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_b) as session:
            session.add(MessageFeedback(message_id=uuid.uuid4(), tenant_id=tenant_a, rating="up"))
    # ...and even the owner (no RLS) cannot attach B's feedback to A's message.
    with pytest.raises(IntegrityError, match="fk_message_feedback_message_id_tenant_id_messages"):  # noqa: SIM117
        with Session(owner_engine) as session, session.begin():
            session.execute(
                MessageFeedback.__table__.delete().where(MessageFeedback.message_id == message_id)
            )
            session.add(MessageFeedback(message_id=message_id, tenant_id=tenant_b, rating="down"))


def test_feedback_goes_with_its_message(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    client.put(_url(turn["conversation_id"], turn["message_id"]), json={"rating": "down"})

    # A retry replaces the turn: the rating of the old answer goes with it.
    retried = ask(client, PM_QUESTION, conversation_id=turn["conversation_id"], retry=True)
    assert retried["message_id"] != turn["message_id"]
    assert _feedback_rows(app_sessions, tenant_a) == []

    client.put(_url(turn["conversation_id"], retried["message_id"]), json={"rating": "up"})
    assert client.delete(f"/conversations/{turn['conversation_id']}").status_code == 204
    assert _feedback_rows(app_sessions, tenant_a) == []

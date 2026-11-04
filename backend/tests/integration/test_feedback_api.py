"""Feedback on answers end to end: one rating per answer and rater (upsert, clear),
each rater's own rating and the totals in the conversation, which messages can be rated,
the rater header, tenant isolation through the API and in the database (RLS and the
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

ANA, BEN = uuid.uuid4(), uuid.uuid4()  # two raters (two browsers) of the same tenant


def _as(rater: uuid.UUID) -> dict[str, str]:
    return {"X-Rater-Id": str(rater)}


def _url(conversation_id, message_id) -> str:
    return f"/conversations/{conversation_id}/messages/{message_id}/feedback"


def _feedback_rows(sessions: sessionmaker[Session], tenant_id: uuid.UUID) -> list[MessageFeedback]:
    with tenant_transaction(sessions, tenant_id) as session:
        return session.scalars(select(MessageFeedback).order_by(MessageFeedback.rating)).all()


def _answer(client, conversation_id, rater: uuid.UUID | None = None) -> dict:
    headers = _as(rater) if rater else {}
    messages = client.get(f"/conversations/{conversation_id}", headers=headers).json()["messages"]
    question, answer = messages[-2], messages[-1]
    assert question["feedback"] is None and question["feedback_totals"] is None
    return answer


def test_rating_is_upserted_per_rater(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])

    # Not rated yet: no own feedback, zero totals.
    fresh = _answer(client, turn["conversation_id"], ANA)
    assert fresh["feedback"] is None
    assert fresh["feedback_totals"] == {"up": 0, "down": 0}

    first = client.put(url, json={"rating": "up"}, headers=_as(ANA))
    assert first.status_code == 200, first.text
    assert first.json() == {
        "feedback": {"rating": "up", "comment": None},
        "totals": {"up": 1, "down": 0},
    }

    [before] = _feedback_rows(app_sessions, tenant_a)
    second = client.put(
        url, json={"rating": "down", "comment": "  Wrong annex.  "}, headers=_as(ANA)
    )
    assert second.json() == {
        "feedback": {"rating": "down", "comment": "Wrong annex."},
        "totals": {"up": 0, "down": 1},
    }
    [after] = _feedback_rows(app_sessions, tenant_a)  # still one row for Ana: replaced
    assert (after.rater_id, after.rating, after.comment) == (ANA, "down", "Wrong annex.")
    assert after.created_at == before.created_at
    assert after.updated_at > before.updated_at


def test_two_raters_each_keep_their_own_and_both_count(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])

    client.put(url, json={"rating": "up", "comment": "Exact clause."}, headers=_as(ANA))
    both = client.put(url, json={"rating": "down"}, headers=_as(BEN))
    assert both.json()["totals"] == {"up": 1, "down": 1}
    assert len(_feedback_rows(app_sessions, tenant_a)) == 2

    ana = _answer(client, turn["conversation_id"], ANA)
    ben = _answer(client, turn["conversation_id"], BEN)
    anonymous = _answer(client, turn["conversation_id"])
    assert ana["feedback"] == {"rating": "up", "comment": "Exact clause."}
    assert ben["feedback"] == {"rating": "down", "comment": None}
    assert anonymous["feedback"] is None  # without the header, nobody's own
    for answer in (ana, ben, anonymous):
        assert answer["feedback_totals"] == {"up": 1, "down": 1}

    # Clearing Ben's keeps Ana's.
    cleared = client.delete(url, headers=_as(BEN))
    assert cleared.status_code == 200
    assert cleared.json() == {"feedback": None, "totals": {"up": 1, "down": 0}}
    [left] = _feedback_rows(app_sessions, tenant_a)
    assert left.rater_id == ANA
    assert _answer(client, turn["conversation_id"], ANA)["feedback"]["rating"] == "up"
    assert _answer(client, turn["conversation_id"], BEN)["feedback"] is None
    assert client.delete(url, headers=_as(BEN)).status_code == 200  # nothing left: still fine


def test_a_missing_or_invalid_rater_id_is_refused(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])

    for headers in ({}, {"X-Rater-Id": "not-a-uuid"}):
        put = client.put(url, json={"rating": "up"}, headers=headers)
        assert put.status_code == 422
        assert put.headers["content-type"].startswith("application/problem+json")
        assert client.delete(url, headers=headers).status_code == 422
    assert _feedback_rows(app_sessions, tenant_a) == []


def test_only_answers_of_that_conversation_can_be_rated(setup) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    other = ask(client, INCIDENT_QUESTION)  # another conversation of the same tenant
    detail = client.get(f"/conversations/{first['conversation_id']}").json()
    question_id = detail["messages"][0]["id"]

    question = client.put(
        _url(first["conversation_id"], question_id), json={"rating": "up"}, headers=_as(ANA)
    )
    assert question.status_code == 422
    assert question.headers["content-type"].startswith("application/problem+json")
    assert (
        client.delete(_url(first["conversation_id"], question_id), headers=_as(ANA)).status_code
        == 422
    )

    elsewhere = client.put(
        _url(other["conversation_id"], first["message_id"]), json={"rating": "up"}, headers=_as(ANA)
    )
    assert elsewhere.status_code == 404
    unknown = client.put(
        _url(first["conversation_id"], uuid.uuid4()), json={"rating": "up"}, headers=_as(ANA)
    )
    assert unknown.status_code == 404
    assert unknown.json()["title"] == "Not found"

    invalid = client.put(
        _url(first["conversation_id"], first["message_id"]),
        json={"rating": "up", "comment": "x" * 1001},
        headers=_as(ANA),
    )
    assert invalid.status_code == 422


def test_another_tenant_cannot_rate_nor_see_the_totals(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, tenant_b, *_ = setup
    client_a, client_b = make(tenant_a), make(tenant_b)
    turn_a = ask(client_a, PM_QUESTION)
    turn_b = ask(client_b, INCIDENT_QUESTION)
    url_a = _url(turn_a["conversation_id"], turn_a["message_id"])
    client_a.put(url_a, json={"rating": "up", "comment": "Exact clause."}, headers=_as(ANA))

    # The same rater id from another tenant changes nothing.
    assert client_b.put(url_a, json={"rating": "down"}, headers=_as(ANA)).status_code == 404
    assert client_b.delete(url_a, headers=_as(ANA)).status_code == 404
    mixed = _url(turn_b["conversation_id"], turn_a["message_id"])
    assert client_b.put(mixed, json={"rating": "down"}, headers=_as(BEN)).status_code == 404
    # Neither A's conversation nor its totals are readable from B.
    assert client_b.get(f"/conversations/{turn_a['conversation_id']}").status_code == 404
    assert _answer(client_b, turn_b["conversation_id"], ANA)["feedback_totals"] == {
        "up": 0,
        "down": 0,
    }

    [row] = _feedback_rows(app_sessions, tenant_a)
    assert (row.rater_id, row.rating, row.comment) == (ANA, "up", "Exact clause.")
    assert _feedback_rows(app_sessions, tenant_b) == []


def test_rls_and_the_composite_fk_keep_feedback_in_its_tenant(
    setup,  # noqa: F811
    app_sessions: sessionmaker[Session],
    owner_engine: Engine,
) -> None:
    make, tenant_a, tenant_b, *_ = setup
    turn = ask(make(tenant_a), PM_QUESTION)
    message_id = uuid.UUID(turn["message_id"])
    make(tenant_a).put(
        _url(turn["conversation_id"], message_id), json={"rating": "up"}, headers=_as(ANA)
    )

    # No WHERE tenant_id at all: the policy hides A's row from B.
    with tenant_transaction(app_sessions, tenant_b) as session:
        assert session.scalar(select(func.count()).select_from(MessageFeedback)) == 0
    # Without a tenant, nothing is visible.
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(MessageFeedback)) == 0

    # The app role cannot write a row for another tenant...
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_b) as session:
            session.add(
                MessageFeedback(
                    message_id=uuid.uuid4(), rater_id=BEN, tenant_id=tenant_a, rating="up"
                )
            )
    # ...and even the owner (no RLS) cannot attach B's feedback to A's message.
    with pytest.raises(IntegrityError, match="fk_message_feedback_message_id_tenant_id_messages"):  # noqa: SIM117
        with Session(owner_engine) as session, session.begin():
            session.add(
                MessageFeedback(
                    message_id=message_id, rater_id=BEN, tenant_id=tenant_b, rating="down"
                )
            )


def test_feedback_goes_with_its_message(setup, app_sessions) -> None:  # noqa: F811
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    turn = ask(client, PM_QUESTION)
    url = _url(turn["conversation_id"], turn["message_id"])
    client.put(url, json={"rating": "down"}, headers=_as(ANA))
    client.put(url, json={"rating": "up"}, headers=_as(BEN))

    # A retry replaces the turn: the ratings of the old answer go with it.
    retried = ask(client, PM_QUESTION, conversation_id=turn["conversation_id"], retry=True)
    assert retried["message_id"] != turn["message_id"]
    assert _feedback_rows(app_sessions, tenant_a) == []

    client.put(
        _url(turn["conversation_id"], retried["message_id"]),
        json={"rating": "up"},
        headers=_as(ANA),
    )
    assert client.delete(f"/conversations/{turn['conversation_id']}").status_code == 204
    assert _feedback_rows(app_sessions, tenant_a) == []

"""Conversations end to end: POST /query and /query/stream store turns, the conversation
endpoints, retry, follow-ups, interrupted turns, suggestions and generated titles, and
tenant isolation. Real Postgres, fake embeddings and fake LLM (the title and suggestion
generators are scripted); authentication is overridden with a test tenant (as in
test_query_api.py)."""

import asyncio
import dataclasses
import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api import query as query_api
from app.api.deps import (
    TenantContext,
    get_current_tenant,
    get_session_factory,
    get_tenant_session,
)
from app.conversations.service import plan_turn, store_generated_title
from app.core.config import Settings
from app.db.models import Conversation, Message
from app.db.session import tenant_transaction
from app.main import create_app
from tests.integration.test_retrieval import PM_A, SHARED, add_document

TIRES = (
    "4.2 Late delivery penalty. If tires are delivered late, Supplier shall pay a penalty "
    "of 1.5% of the order value per business day of delay. The penalty is capped at 15% "
    "of the order value."
)
PM_QUESTION = "How often must the Supplier perform a PM-A service on each tractor?"
INCIDENT_QUESTION = "What does Incident Reporting Protocol RV-IRP-7 require?"
TIRE_QUESTION = "What is the penalty for late tire delivery?"


@pytest.fixture
def setup(app_sessions: sessionmaker[Session], two_tenants):
    """Tenant A: maintenance and tires documents. Tenant B: a carrier agreement. Returns
    a factory of clients acting as a tenant, plus the ids."""
    tenant_a, tenant_b = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        maintenance = add_document(session, tenant_a, "maintenance.pdf", [PM_A, SHARED])
        tires = add_document(session, tenant_a, "tires.pdf", [TIRES])
    with tenant_transaction(app_sessions, tenant_b) as session:
        add_document(session, tenant_b, "carrier.pdf", [SHARED])

    def make(tenant_id: uuid.UUID | None, **settings: Any) -> TestClient:
        """`tenant_id=None`: no authentication override (requests need a real token)."""
        app = create_app(Settings(_env_file=None, **settings))
        app.dependency_overrides[get_session_factory] = lambda: app_sessions
        if tenant_id is not None:

            def session_override() -> Iterator[Session]:
                with tenant_transaction(app_sessions, tenant_id) as session:
                    yield session

            app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
                id=tenant_id, name=f"test-{tenant_id}"
            )
            app.dependency_overrides[get_tenant_session] = session_override
        return TestClient(app)

    return make, tenant_a, tenant_b, maintenance.id, tires.id


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """[(event name, JSON data)], skipping keep-alive comments."""
    events = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def stored_messages(sessions: sessionmaker[Session], tenant_id: uuid.UUID, conversation_id):
    with tenant_transaction(sessions, tenant_id) as session:
        return session.scalars(
            select(Message)
            .where(Message.conversation_id == uuid.UUID(str(conversation_id)))
            .order_by(Message.sequence)
        ).all()


def ask(client: TestClient, question: str, **extra: Any) -> dict:
    response = client.post("/query", json={"question": question, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --- Stream -------------------------------------------------------------------------------


def test_stream_sends_conversation_phases_then_result(setup, app_sessions) -> None:
    make, tenant_a, _, maintenance_id, _ = setup
    response = make(tenant_a).post("/query/stream", json={"question": PM_QUESTION})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names == ["conversation", "phase", "phase", "phase", "result"]

    _, conversation = events[0]
    assert conversation["title"] == PM_QUESTION
    phases = [data for name, data in events if name == "phase"]
    assert [p["phase"] for p in phases] == ["searching", "drafting", "validating"]
    assert phases[0]["label"] == "Searching your documents"

    _, result = events[-1]
    assert result["status"] == "answered"
    assert result["conversation_id"] == conversation["conversation_id"]
    assert result["citations"][0]["document_id"] == str(maintenance_id)
    assert result["details"]["retrieved"]
    # Saved before the result was sent.
    messages = stored_messages(app_sessions, tenant_a, result["conversation_id"])
    assert [m.role for m in messages] == ["user", "assistant"]
    assert str(messages[1].id) == result["message_id"]


def test_stream_timeout_ends_with_an_error_event_and_stores_nothing(setup, app_sessions) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a, fake_llm_mode="timeout", llm_max_retries=0)
    response = client.post("/query/stream", json={"question": PM_QUESTION})

    assert response.status_code == 200  # the stream had started
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["conversation", "phase", "phase", "error"]
    _, error = events[-1]
    assert error["status"] == 504
    assert error["type"] == "/problems/upstream-timeout"
    assert set(error) == {"type", "title", "status", "detail", "trace_id"}

    conversation_id = uuid.UUID(events[0][1]["conversation_id"])
    with tenant_transaction(app_sessions, tenant_a) as session:
        assert session.get(Conversation, conversation_id) is None
    assert client.get("/conversations").json()["count"] == 0


def test_stream_errors_before_the_stream_starts(setup) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)

    invalid = client.post("/query/stream", json={"question": "   "})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"] == "application/problem+json"

    missing = client.post(
        "/query/stream", json={"question": PM_QUESTION, "conversation_id": str(uuid.uuid4())}
    )
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"

    unauthenticated = make(None).post("/query/stream", json={"question": PM_QUESTION})
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["content-type"] == "application/problem+json"


# --- Persistence --------------------------------------------------------------------------


def test_query_stores_the_turn(setup, app_sessions) -> None:
    make, tenant_a, _, maintenance_id, _ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    second = ask(client, INCIDENT_QUESTION, conversation_id=first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]

    messages = stored_messages(app_sessions, tenant_a, first["conversation_id"])
    assert [(m.sequence, m.role) for m in messages] == [
        (1, "user"),
        (2, "assistant"),
        (3, "user"),
        (4, "assistant"),
    ]
    user, assistant = messages[0], messages[1]
    assert user.content == PM_QUESTION
    assert user.status is None and user.details is None
    assert assistant.status == "answered"
    assert assistant.citations == first["citations"]  # copied, filename included
    assert assistant.citations[0]["document_id"] == str(maintenance_id)
    assert assistant.details == first["details"]
    assert assistant.llm_model == first["details"]["llm_model"]
    assert assistant.tokens_in is not None and assistant.latency_ms is not None

    detail = client.get(f"/conversations/{first['conversation_id']}").json()
    assert detail["title"] == PM_QUESTION
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] * 2
    assert detail["messages"][1]["id"] == first["message_id"]
    assert detail["messages"][1]["citations"] == first["citations"]
    assert detail["messages"][0]["details"] is None
    assert detail["messages"][0]["citations"] == []


def test_details_never_contain_chunk_content(setup) -> None:
    make, tenant_a, *_ = setup
    body = ask(make(tenant_a), PM_QUESTION)
    details = json.dumps(body["details"])
    assert body["details"]["retrieved"]
    assert "content" not in body["details"]["retrieved"][0]
    for text in (PM_A, SHARED, TIRES):
        assert text not in details
        assert text[:40] not in details


def test_insufficient_evidence_turn_is_stored(setup, app_sessions) -> None:
    make, tenant_a, *_ = setup
    body = ask(make(tenant_a), "zebra quantum giraffe?")
    assert body["status"] == "insufficient_evidence"
    assert body["details"]["insufficient_reason"] is not None
    messages = stored_messages(app_sessions, tenant_a, body["conversation_id"])
    assert messages[1].status == "insufficient_evidence"


def test_long_question_title_is_cut(setup) -> None:
    make, tenant_a, *_ = setup
    question = "What does the agreement say about " + "maintenance " * 20 + "?"
    body = ask(make(tenant_a), question)
    title = make(tenant_a).get(f"/conversations/{body['conversation_id']}").json()["title"]
    assert len(title) == 80
    assert question.startswith(title)


# --- Follow-ups ---------------------------------------------------------------------------


def test_follow_up_uses_the_conversation_context(setup, app_sessions) -> None:
    make, tenant_a, _, _, tires_id = setup
    client = make(tenant_a)
    first = ask(client, TIRE_QUESTION)
    assert first["details"]["uses_context"] is False
    assert first["details"]["standalone_question"] is None

    response = client.post(
        "/query/stream",
        json={"question": "and what is the cap?", "conversation_id": first["conversation_id"]},
    )
    events = parse_sse(response.text)
    phases = [data["phase"] for name, data in events if name == "phase"]
    assert phases[:2] == ["contextualizing", "searching"]
    assert events[1][1]["label"] == "Understanding your follow-up"

    _, result = events[-1]
    details = result["details"]
    assert details["uses_context"] is True
    assert details["standalone_question"] == f"{TIRE_QUESTION} and what is the cap?"
    assert details["retrieved"][0]["document_id"] == str(tires_id)

    messages = stored_messages(app_sessions, tenant_a, first["conversation_id"])
    assert messages[2].content == "and what is the cap?"  # as typed
    assert messages[3].standalone_question == details["standalone_question"]
    assert messages[1].standalone_question is None


# --- Retry --------------------------------------------------------------------------------


def test_retry_replaces_the_last_turn(setup, app_sessions) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    conversation_id = first["conversation_id"]
    second = ask(client, INCIDENT_QUESTION, conversation_id=conversation_id)

    retried = ask(client, INCIDENT_QUESTION, conversation_id=conversation_id, retry=True)
    assert retried["message_id"] != second["message_id"]

    messages = stored_messages(app_sessions, tenant_a, conversation_id)
    assert [(m.sequence, m.content) for m in messages if m.role == "user"] == [
        (1, PM_QUESTION),
        (3, INCIDENT_QUESTION),
    ]
    assert str(messages[3].id) == retried["message_id"]
    assert str(messages[1].id) == first["message_id"]  # the earlier turn is untouched


def test_retry_errors(setup) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)

    different = client.post(
        "/query",
        json={"question": "Something else?", "conversation_id": first["conversation_id"]}
        | {"retry": True},
    )
    assert different.status_code == 422
    no_conversation = client.post("/query", json={"question": PM_QUESTION, "retry": True})
    assert no_conversation.status_code == 422
    stream = client.post(
        "/query/stream",
        json={"question": "Something else?", "conversation_id": first["conversation_id"]}
        | {"retry": True},
    )
    assert stream.status_code == 422


# --- Listing, rename, delete --------------------------------------------------------------


def test_listing_order_search_and_pagination(setup) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    pm = ask(client, PM_QUESTION)
    incident = ask(client, INCIDENT_QUESTION)
    tires = ask(client, TIRE_QUESTION)
    # A new turn moves the PM conversation to the top.
    ask(client, "What about the PM-B service?", conversation_id=pm["conversation_id"])

    listing = client.get("/conversations").json()
    assert listing["count"] == 3
    assert (listing["limit"], listing["offset"]) == (20, 0)
    ids = [c["id"] for c in listing["results"]]
    assert ids == [pm["conversation_id"], tires["conversation_id"], incident["conversation_id"]]
    assert listing["results"][0]["message_count"] == 4
    assert listing["results"][1]["message_count"] == 2

    page = client.get("/conversations", params={"limit": 1, "offset": 1}).json()
    assert page["count"] == 3
    assert [c["id"] for c in page["results"]] == [tires["conversation_id"]]

    found = client.get("/conversations", params={"search": "rv-irp"}).json()
    assert [c["id"] for c in found["results"]] == [incident["conversation_id"]]
    # Wildcards are literal.
    assert client.get("/conversations", params={"search": "%"}).json()["count"] == 0
    assert client.get("/conversations", params={"limit": 101}).status_code == 422


def test_rename(setup) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    body = ask(client, PM_QUESTION)
    url = f"/conversations/{body['conversation_id']}"
    before = client.get(url).json()

    renamed = client.patch(url, json={"title": "  Tractor maintenance  "})
    assert renamed.status_code == 200
    summary = renamed.json()
    assert summary["title"] == "Tractor maintenance"
    assert summary["message_count"] == 2
    assert summary["updated_at"] == before["updated_at"]  # a rename is not activity
    assert client.get(url).json()["title"] == "Tractor maintenance"

    assert client.patch(url, json={"title": "   "}).status_code == 422
    assert client.patch(url, json={"title": "x" * 121}).status_code == 422


def test_delete(setup, app_sessions) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    body = ask(client, PM_QUESTION)
    url = f"/conversations/{body['conversation_id']}"

    response = client.delete(url)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404
    assert stored_messages(app_sessions, tenant_a, body["conversation_id"]) == []


# --- Tenant isolation ---------------------------------------------------------------------


def test_another_tenants_conversation_is_not_found(setup) -> None:
    make, tenant_a, tenant_b, *_ = setup
    client_a, client_b = make(tenant_a), make(tenant_b)
    body_a = ask(client_a, PM_QUESTION)
    body_b = ask(client_b, INCIDENT_QUESTION)
    url = f"/conversations/{body_a['conversation_id']}"

    assert client_b.get(url).status_code == 404
    assert client_b.patch(url, json={"title": "mine now"}).status_code == 404
    assert client_b.delete(url).status_code == 404
    stream = client_b.post(
        "/query/stream",
        json={"question": INCIDENT_QUESTION, "conversation_id": body_a["conversation_id"]},
    )
    assert stream.status_code == 404
    joined = client_b.post(
        "/query",
        json={"question": INCIDENT_QUESTION, "conversation_id": body_a["conversation_id"]},
    )
    assert joined.status_code == 404

    # A's conversation is intact, and each list only has its own.
    assert client_a.get(url).json()["title"] == PM_QUESTION
    ids_a = [c["id"] for c in client_a.get("/conversations").json()["results"]]
    ids_b = [c["id"] for c in client_b.get("/conversations").json()["results"]]
    assert ids_a == [body_a["conversation_id"]]
    assert ids_b == [body_b["conversation_id"]]


# --- Interrupted turns (client disconnect) ------------------------------------------------


def _hold_the_answer(monkeypatch) -> threading.Event:
    """The answer waits for the returned event before it starts, so the client can
    leave while it is in progress."""
    gate = threading.Event()
    real_run_turn = query_api.run_turn

    def slow_run_turn(*args: Any, **kwargs: Any):
        assert gate.wait(10)
        return real_run_turn(*args, **kwargs)

    monkeypatch.setattr(query_api, "run_turn", slow_run_turn)
    return gate


def stream_then_disconnect(client: TestClient, body: dict) -> list[tuple[str, dict]]:
    """POST /query/stream through the ASGI interface, and disconnect as soon as the
    `conversation` event arrives (what the browser does on Stop). The TestClient cannot
    do this: it reads the whole body. Returns the events received."""
    received = bytearray()

    async def run() -> None:
        disconnect = asyncio.Event()
        body_sent = False

        async def receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": json.dumps(body).encode()}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body":
                received.extend(message.get("body", b""))
                if b"event: conversation" in received:
                    disconnect.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/query/stream",
            "raw_path": b"/query/stream",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        await client.app(scope, receive, send)

    asyncio.run(run())
    return parse_sse(received.decode())


def _join_stream_threads() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith("query-stream"):
            thread.join(30)


def test_disconnect_stores_the_question_as_an_interrupted_turn(
    setup, app_sessions, monkeypatch
) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    gate = _hold_the_answer(monkeypatch)

    events = stream_then_disconnect(client, {"question": PM_QUESTION})
    gate.set()  # the answer completes after the client left: it is discarded
    _join_stream_threads()

    assert [name for name, _ in events] == ["conversation"]
    conversation_id = events[0][1]["conversation_id"]
    messages = stored_messages(app_sessions, tenant_a, conversation_id)
    assert [(m.sequence, m.role, m.content, m.status) for m in messages] == [
        (1, "user", PM_QUESTION, None),
        (2, "assistant", "", "interrupted"),
    ]
    # The new conversation exists, titled with the question, so it can be found again.
    detail = client.get(f"/conversations/{conversation_id}").json()
    assert detail["title"] == PM_QUESTION
    assert detail["messages"][1]["status"] == "interrupted"
    assert detail["messages"][1]["details"] is None


def test_an_interrupted_turn_can_be_asked_again_and_is_not_context(
    setup, app_sessions, monkeypatch
) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, TIRE_QUESTION)
    conversation_id = first["conversation_id"]

    gate = _hold_the_answer(monkeypatch)
    stream_then_disconnect(client, {"question": PM_QUESTION, "conversation_id": conversation_id})
    gate.set()
    _join_stream_threads()
    monkeypatch.undo()

    # A follow-up is resolved against the last ANSWERED turn, not the interrupted one.
    follow_up = ask(client, "and what is the cap?", conversation_id=conversation_id)
    assert follow_up["details"]["standalone_question"] == f"{TIRE_QUESTION} and what is the cap?"

    # The interrupted turn stays where it was; the follow-up comes after it.
    statuses = [m.status for m in stored_messages(app_sessions, tenant_a, conversation_id)]
    assert statuses == [None, "answered", None, "interrupted", None, "answered"]


def test_retry_replaces_an_interrupted_last_turn(setup, app_sessions, monkeypatch) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    gate = _hold_the_answer(monkeypatch)
    events = stream_then_disconnect(client, {"question": PM_QUESTION})
    gate.set()
    _join_stream_threads()
    monkeypatch.undo()
    conversation_id = events[0][1]["conversation_id"]

    retried = ask(client, PM_QUESTION, conversation_id=conversation_id, retry=True)

    messages = stored_messages(app_sessions, tenant_a, conversation_id)
    assert [(m.role, m.status) for m in messages] == [("user", None), ("assistant", "answered")]
    assert str(messages[1].id) == retried["message_id"]


def test_an_interrupted_retry_keeps_the_turn_it_was_replacing(
    setup, app_sessions, monkeypatch
) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    conversation_id = first["conversation_id"]

    gate = _hold_the_answer(monkeypatch)
    stream_then_disconnect(
        client, {"question": PM_QUESTION, "conversation_id": conversation_id, "retry": True}
    )
    gate.set()
    _join_stream_threads()

    messages = stored_messages(app_sessions, tenant_a, conversation_id)
    assert [m.status for m in messages] == [None, "answered"]
    assert str(messages[1].id) == first["message_id"]


# --- Suggestions and generated titles -----------------------------------------------------


class ScriptedSuggester:
    def __init__(self, suggestions: list[str]) -> None:
        self.suggestions = suggestions
        self.calls: list[str] = []

    def suggest(self, question, answer, citations):
        self.calls.append(question)
        return self.suggestions


class ScriptedTitler:
    def __init__(self, title: str | None) -> None:
        self.result = title

    def title(self, question, answer):
        return self.result


def test_stream_sends_suggestions_and_title_after_the_result(setup, monkeypatch) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    suggester = ScriptedSuggester(["Who performs the PM-B service?"])
    monkeypatch.setattr(query_api, "get_suggestion_generator", lambda settings: suggester)
    monkeypatch.setattr(
        query_api, "get_title_generator", lambda settings: ScriptedTitler("PM-A service interval")
    )

    events = parse_sse(client.post("/query/stream", json={"question": PM_QUESTION}).text)

    names = [name for name, _ in events]
    assert names == ["conversation", "phase", "phase", "phase", "result", "suggestions", "title"]
    result = events[4][1]
    assert events[5][1] == {
        "message_id": result["message_id"],
        "suggestions": ["Who performs the PM-B service?"],
    }
    assert events[6][1]["title"] == "PM-A service interval"
    assert suggester.calls == [PM_QUESTION]

    # Stored: after a reload the last answer has its suggestions and the new title.
    detail = client.get(f"/conversations/{result['conversation_id']}").json()
    assert detail["title"] == "PM-A service interval"
    assert detail["messages"][1]["suggestions"] == ["Who performs the PM-B service?"]
    assert "suggestions" not in detail["messages"][1]["details"]
    assert detail["messages"][0]["suggestions"] == []


def test_no_suggestions_for_insufficient_evidence_and_keyless_sends_none(
    setup, monkeypatch
) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    # Keyless (the default): no extra events at all.
    events = parse_sse(client.post("/query/stream", json={"question": PM_QUESTION}).text)
    assert [name for name, _ in events][-1] == "result"

    suggester = ScriptedSuggester(["Anything else?"])
    monkeypatch.setattr(query_api, "get_suggestion_generator", lambda settings: suggester)
    events = parse_sse(
        client.post("/query/stream", json={"question": "zebra quantum giraffe?"}).text
    )
    assert events[-1][0] == "result" and events[-1][1]["status"] == "insufficient_evidence"
    assert suggester.calls == []


def test_a_renamed_conversation_keeps_its_title(setup, app_sessions) -> None:
    make, tenant_a, *_ = setup
    client = make(tenant_a)
    first = ask(client, PM_QUESTION)
    conversation_id = uuid.UUID(first["conversation_id"])
    # The plan as it was for the first turn: the provisional title is the question.
    with tenant_transaction(app_sessions, tenant_a) as session:
        plan = plan_turn(session, tenant_a, conversation_id=None, question=PM_QUESTION, retry=False)
    plan = dataclasses.replace(plan, conversation_id=conversation_id)

    client.patch(f"/conversations/{conversation_id}", json={"title": "My maintenance notes"})
    with tenant_transaction(app_sessions, tenant_a) as session:
        assert store_generated_title(session, tenant_a, plan, "PM-A interval") is False
    assert client.get(f"/conversations/{conversation_id}").json()["title"] == (
        "My maintenance notes"
    )

    other = ask(client, INCIDENT_QUESTION)
    with tenant_transaction(app_sessions, tenant_a) as session:
        plan = dataclasses.replace(
            plan, conversation_id=uuid.UUID(other["conversation_id"]), title=INCIDENT_QUESTION
        )
        assert store_generated_title(session, tenant_a, plan, "Incident reporting") is True
    assert client.get(f"/conversations/{other['conversation_id']}").json()["title"] == (
        "Incident reporting"
    )

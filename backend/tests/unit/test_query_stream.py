"""The stream's own logic, without a database: event order after the result, and what a
client disconnect stores. The answer, the storage functions and the transaction are
replaced by doubles; the generator and its worker thread are the real ones.
(The same flows against Postgres: tests/integration/test_conversations_api.py.)"""

import asyncio
import threading
import uuid
from contextlib import contextmanager
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api import query as stream
from app.conversations.service import TurnPlan
from app.core import tracing
from app.core.config import Settings
from app.core.errors import ServiceUnavailable
from app.schemas.query import QueryResponse

QUESTION = "What is the penalty for late tire delivery?"
TENANT = uuid.uuid4()


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True


class Doubles:
    """Records what the stream stored. `release` lets the answer (or the suggestions)
    finish when the test says so."""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.interrupted: list[tuple[TurnPlan, str]] = []
        self.suggestions: list[list[str]] = []
        self.titles: list[str] = []
        self.answer_release = threading.Event()
        self.answer_release.set()
        self.suggest_release = threading.Event()
        self.suggest_release.set()
        self.answer_error: Exception | None = None

    @contextmanager
    def transaction(self, factory, tenant_id):
        session = FakeSession()
        self.sessions.append(session)
        try:
            yield session
        except BaseException:
            session.rolled_back = True
            raise


def _response(plan: TurnPlan, status: str = "answered") -> QueryResponse:
    return QueryResponse(
        conversation_id=plan.conversation_id,
        answer="1.5% per business day, capped at 15%.",
        status=status,
        confidence="high",
        citations=[],
        warnings=[],
        message_id=uuid.uuid4(),
    )


class Suggester:
    def __init__(self, doubles: Doubles, result: list[str]) -> None:
        self.doubles = doubles
        self.result = result

    def suggest(self, question, answer, citations):
        self.doubles.suggest_release.wait(5)
        return self.result


class Titler:
    def __init__(self, result: str | None) -> None:
        self.result = result

    def title(self, question, answer):
        return self.result


@pytest.fixture
def doubles(monkeypatch) -> Doubles:
    d = Doubles()

    def run_turn(session, *, plan, on_phase, **_: Any) -> QueryResponse:
        on_phase("searching")
        d.answer_release.wait(5)
        if d.answer_error:
            raise d.answer_error
        return _response(plan)

    monkeypatch.setattr(stream, "tenant_transaction", d.transaction)
    monkeypatch.setattr(stream, "run_turn", run_turn)
    monkeypatch.setattr(stream, "get_embeddings", lambda settings: None)
    monkeypatch.setattr(stream, "get_llm", lambda settings, embeddings: None)
    monkeypatch.setattr(stream, "get_resolver", lambda settings: None)
    monkeypatch.setattr(stream, "get_judge", lambda settings: None)
    monkeypatch.setattr(
        stream,
        "save_interrupted_turn",
        lambda session, tenant_id, plan, question: d.interrupted.append((plan, question)),
    )

    def store_suggestions(session, tenant_id, message_id, suggestions):
        d.suggestions.append(suggestions)
        return True

    def store_generated_title(session, tenant_id, plan, title):
        d.titles.append(title)
        return True

    monkeypatch.setattr(stream, "store_suggestions", store_suggestions)
    monkeypatch.setattr(stream, "store_generated_title", store_generated_title)
    # Keyless by default.
    monkeypatch.setattr(stream, "get_suggestion_generator", lambda settings: None)
    monkeypatch.setattr(stream, "get_title_generator", lambda settings: None)
    return d


def _plan(is_new: bool = True) -> TurnPlan:
    return TurnPlan(conversation_id=uuid.uuid4(), title=QUESTION, is_new=is_new, context=[])


def _events(chunks: list[str]) -> list[str]:
    return [chunk.split("\n", 1)[0].removeprefix("event: ") for chunk in chunks]


def _join_threads() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith("query-stream"):
            thread.join(5)


def _stream(plan: TurnPlan) -> Any:
    return stream._stream_events(plan, QUESTION, TENANT, None, Settings(_env_file=None))


def _read_all(plan: TurnPlan) -> list[str]:
    async def read() -> list[str]:
        return [chunk async for chunk in _stream(plan)]

    chunks = asyncio.run(read())
    _join_threads()
    return chunks


def _read_then_disconnect(plan: TurnPlan, events_before: int) -> list[str]:
    """Read `events_before` events, then cancel the generator while it waits (what
    Starlette does when the client disconnects)."""

    async def read() -> list[str]:
        agen = _stream(plan)
        chunks = [await agen.__anext__() for _ in range(events_before)]
        waiting = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        return chunks

    return asyncio.run(read())


def test_keyless_stream_ends_with_the_result(doubles: Doubles) -> None:
    plan = _plan()
    assert _events(_read_all(plan)) == ["conversation", "phase", "result"]
    assert [s.committed for s in doubles.sessions] == [True]
    assert doubles.suggestions == [] and doubles.titles == [] and doubles.interrupted == []


def test_suggestions_then_title_follow_the_result(doubles: Doubles, monkeypatch) -> None:
    monkeypatch.setattr(
        stream,
        "get_suggestion_generator",
        lambda settings: Suggester(doubles, ["Is there a cap?"]),
    )
    monkeypatch.setattr(stream, "get_title_generator", lambda settings: Titler("Tire penalty"))
    chunks = _read_all(_plan())

    assert _events(chunks) == ["conversation", "phase", "result", "suggestions", "title"]
    assert '"suggestions":["Is there a cap?"]' in chunks[3]
    assert '"title":"Tire penalty"' in chunks[4]
    assert doubles.suggestions == [["Is there a cap?"]]
    assert doubles.titles == ["Tire penalty"]


def test_no_title_for_an_existing_conversation_and_no_event_without_suggestions(
    doubles: Doubles, monkeypatch
) -> None:
    monkeypatch.setattr(stream, "get_suggestion_generator", lambda settings: Suggester(doubles, []))
    monkeypatch.setattr(stream, "get_title_generator", lambda settings: Titler("Tire penalty"))
    assert _events(_read_all(_plan(is_new=False))) == ["conversation", "phase", "result"]
    assert doubles.titles == []


def test_a_storage_failure_after_the_result_is_never_an_error_event(
    doubles: Doubles, monkeypatch
) -> None:
    monkeypatch.setattr(
        stream, "get_suggestion_generator", lambda settings: Suggester(doubles, ["Is it capped?"])
    )

    def broken(*args: Any) -> bool:
        raise RuntimeError("database gone")

    monkeypatch.setattr(stream, "store_suggestions", broken)
    assert _events(_read_all(_plan())) == ["conversation", "phase", "result"]


def test_a_provider_error_is_an_error_event_and_nothing_is_stored(doubles: Doubles) -> None:
    doubles.answer_error = ServiceUnavailable("The language model is not available.")
    assert _events(_read_all(_plan())) == ["conversation", "phase", "error"]
    assert [s.committed for s in doubles.sessions] == [False]
    assert doubles.interrupted == []


def test_disconnect_before_the_answer_stores_an_interrupted_turn(
    doubles: Doubles, monkeypatch
) -> None:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    doubles.answer_release.clear()  # the answer is still being written
    plan = _plan()

    with provider.get_tracer("test").start_as_current_span("POST /query/stream"):
        chunks = _read_then_disconnect(plan, events_before=2)
    doubles.answer_release.set()  # ... and completes after the client left
    _join_threads()

    assert _events(chunks) == ["conversation", "phase"]
    assert doubles.interrupted == [(plan, QUESTION)]
    worker, interrupted = doubles.sessions  # the worker's session, then its own
    assert not worker.committed and worker.rolled_back  # the late answer is discarded
    assert not interrupted.rolled_back

    spans = {s.name: s for s in exporter.get_finished_spans()}
    request = spans["POST /query/stream"]
    assert request.attributes[stream.STREAM_CLIENT_DISCONNECTED] is True
    assert request.attributes[stream.TURN_INTERRUPTED] is True
    assert [event.name for event in request.events] == ["conversation.turn_interrupted"]
    assert "conversation.turn_interrupted" in spans


def test_disconnect_after_the_result_changes_nothing(doubles: Doubles, monkeypatch) -> None:
    monkeypatch.setattr(
        stream, "get_suggestion_generator", lambda settings: Suggester(doubles, ["Is it capped?"])
    )
    doubles.suggest_release.clear()  # the suggestions are still being written

    chunks = _read_then_disconnect(_plan(), events_before=3)
    doubles.suggest_release.set()
    _join_threads()

    assert _events(chunks) == ["conversation", "phase", "result"]
    assert doubles.interrupted == []
    assert [s.committed for s in doubles.sessions][0] is True
    # The suggestions are still stored, for the next reload.
    assert doubles.suggestions == [["Is it capped?"]]


def test_disconnect_after_a_provider_error_stores_nothing(doubles: Doubles) -> None:
    doubles.answer_error = ServiceUnavailable("The language model is not available.")
    plan = _plan()
    turn = stream._Turn()
    turn.fail()
    assert turn.interrupt() is False  # a failed turn is never stored as interrupted
    # And through the stream: the error is queued before the client leaves.
    _read_all(plan)
    assert doubles.interrupted == []


def test_turn_fate_is_decided_once() -> None:
    session = FakeSession()
    turn = stream._Turn()
    assert turn.interrupt() is True
    assert turn.commit(session) is False and not session.committed
    saved = stream._Turn()
    assert saved.commit(session) is True and session.committed
    assert saved.interrupt() is False

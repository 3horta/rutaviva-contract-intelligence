"""Question answering over the tenant's documents, inside a conversation.

- `POST /query`: plain JSON. Answers, stores the turn and returns it.
- `POST /query/stream`: the same, as Server-Sent Events: progress phases, then ONE
  validated result, then (real LLM only) the suggested follow-ups and the conversation's
  generated title. The answer text is never streamed token by token: an answer is only
  shown after its citations were checked.

Both store the turn (question + answer) atomically after a successful answer; on a
503/504 nothing is stored (see `app/conversations/service.py`). If the client of the
stream disconnects before the answer is saved, the question is stored as an
`interrupted` turn instead, and the answer is discarded when it completes.

The title and the suggestions are produced AFTER the turn is saved (and, in the stream,
after `result` is sent): they never delay or fail an answer.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import CurrentTenant, TenantSession, get_session_factory
from app.api.problem import problem_body
from app.conversations.resolver import get_resolver
from app.conversations.service import (
    TurnPlan,
    plan_turn,
    run_turn,
    save_interrupted_turn,
    store_generated_title,
    store_suggestions,
)
from app.conversations.suggestions import get_suggestion_generator
from app.conversations.titles import get_title_generator
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.tracing import TENANT_ID, set_attributes, span
from app.db.session import tenant_transaction
from app.providers import get_embeddings
from app.providers.llm import get_llm
from app.retrieval.judge import get_judge
from app.schemas.conversations import (
    PHASE_LABELS,
    ConversationEvent,
    PhaseEvent,
    SuggestionsEvent,
    TitleEvent,
)
from app.schemas.query import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)

# Span attributes of the request, set when the client of a stream leaves before its end.
STREAM_CLIENT_DISCONNECTED = "stream.client_disconnected"
TURN_INTERRUPTED = "conversation.turn.interrupted"

router = APIRouter(tags=["query"])

# A comment line sent while nothing else happens, so proxies do not close an idle stream.
HEARTBEAT_S = 15.0
_DONE = object()


# A sync handler on purpose: retrieval and the providers are blocking calls, and FastAPI
# runs sync handlers in its thread pool so they do not block the event loop.
@router.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    tenant: CurrentTenant,
    # The turn is written in this transaction, which commits BEFORE the response is
    # sent (TenantSession uses scope="function"): a failed commit is an error, not a 200.
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    background: BackgroundTasks,
) -> QueryResponse:
    # The tenant comes only from the verified token (get_current_tenant), never from
    # the request body; the session already has it set for RLS.
    plan = plan_turn(
        session,
        tenant.id,
        conversation_id=body.conversation_id,
        question=body.question,
        retry=body.retry,
    )
    embeddings = get_embeddings(settings)
    response = run_turn(
        session,
        tenant_id=tenant.id,
        plan=plan,
        question=body.question,
        resolver=get_resolver(settings),
        embeddings=embeddings,
        llm=get_llm(settings, embeddings),
        settings=settings,
        judge=get_judge(settings),
    )
    # After the response is sent (and the turn committed): the title and suggestions
    # are stored for the next reload; this response does not carry them.
    background.add_task(
        after_answer, _discard, plan, body.question, response, tenant.id, factory, settings
    )
    return response


@router.post(
    "/query/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Events: `conversation` (first), `phase` (0..n), then `result` "
                "(a QueryResponse) or `error` (Problem Details). After `result`, with a "
                "real LLM only: `suggestions` (SuggestionsEvent) when there are any, and "
                "`title` (TitleEvent) when a new conversation got a generated title."
            ),
        }
    },
)
def query_stream(
    body: QueryRequest,
    tenant: CurrentTenant,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> StreamingResponse:
    # Validation (422), authentication (401) and a missing or foreign conversation (404)
    # are raised HERE, before the stream starts, as normal Problem Details responses.
    plan = plan_turn(
        session,
        tenant.id,
        conversation_id=body.conversation_id,
        question=body.question,
        retry=body.retry,
    )
    # The request session is closed when this handler returns, before the body is sent,
    # so the stream opens its own transaction (in `_answer_in_background`).
    events = _stream_events(plan, body.question, tenant.id, factory, settings)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sse(event: str, data: str) -> str:
    """One Server-Sent Event; `data` is a single JSON line."""
    return f"event: {event}\ndata: {data}\n\n"


class _Fate(Enum):
    RUNNING = "running"
    SAVED = "saved"  # the answer was committed: a disconnect changes nothing
    FAILED = "failed"  # provider or unexpected error: nothing is stored
    INTERRUPTED = "interrupted"  # the client left first: the question is stored alone


class _Turn:
    """Who decides what is stored: the worker committing the answer, or the client's
    disconnect. Both go through the lock, so exactly one of them wins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fate = _Fate.RUNNING

    def interrupt(self) -> bool:
        """The client left. True if the answer was not saved yet (it will be discarded)."""
        with self._lock:
            if self.fate is not _Fate.RUNNING:
                return False
            self.fate = _Fate.INTERRUPTED
            return True

    def commit(self, session: Session) -> bool:
        """Commit the answered turn unless the client already left (then False, and the
        caller rolls it back). The commit runs under the lock."""
        with self._lock:
            if self.fate is _Fate.INTERRUPTED:
                return False
            session.commit()
            self.fate = _Fate.SAVED
            return True

    def fail(self) -> bool:
        """The answer failed. False if the client had already left: then the failure is
        only the discarded answer colliding with the interrupted turn (or nobody is
        there to see it), not an error to report."""
        with self._lock:
            if self.fate is _Fate.INTERRUPTED:
                return False
            if self.fate is _Fate.RUNNING:
                self.fate = _Fate.FAILED
            return True


class _Discarded(Exception):
    """The client left before the answer was saved: roll the turn back."""


Put = Callable[[object], None]


def _discard(_: object) -> None:
    """`put` for `POST /query`: there is no stream to send events to."""


async def _stream_events(
    plan: TurnPlan,
    question: str,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> AsyncIterator[str]:
    """The answer is one blocking call that reports phases through a callback, so it
    runs in its own thread and hands ready-made events to this generator through an
    asyncio queue. (The alternative, splitting the answer service into resumable steps,
    would complicate the code shared with `POST /query`.)

    If the client disconnects (Stop), Starlette cancels this generator while it waits:
    the `finally` below decides, synchronously (no `await`, so the cancellation cannot
    interrupt it), whether the turn is interrupted. If the answer was not committed yet,
    the question is stored as an interrupted turn by a separate thread with its own
    session, and the worker discards the answer when it completes. If it was already
    committed, nothing changes: the answer shows up when the conversation is reloaded.
    """
    yield sse(
        "conversation",
        ConversationEvent(conversation_id=plan.conversation_id, title=plan.title).model_dump_json(),
    )

    loop = asyncio.get_running_loop()
    events: asyncio.Queue[object] = asyncio.Queue()

    def put(item: object) -> None:
        # A closed loop (RuntimeError) means nobody is listening any more.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(events.put_nowait, item)

    turn = _Turn()
    _start_in_context(
        _answer_in_background,
        (put, turn, plan, question, tenant_id, factory, settings),
        name="query-stream",
    )
    ended = False
    try:
        while True:
            try:
                item = await asyncio.wait_for(events.get(), HEARTBEAT_S)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is _DONE:
                ended = True
                return
            assert isinstance(item, str)
            yield item
    finally:
        if not ended:
            interrupted = turn.interrupt()
            # On the request's span (this runs in the request's context).
            set_attributes(**{STREAM_CLIENT_DISCONNECTED: True, TURN_INTERRUPTED: interrupted})
            if interrupted:
                trace.get_current_span().add_event("conversation.turn_interrupted")
                _start_in_context(
                    _save_interrupted,
                    (plan, question, tenant_id, factory),
                    name="query-stream-interrupted",
                )


def _start_in_context(target: Callable[..., None], args: tuple, *, name: str) -> None:
    """Run `target` in a thread with a copy of the current context, so its spans belong
    to the request's trace (and `trace_id` in the details and errors is the request's).
    Not a daemon: a shutdown waits for it to store what it has."""
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(target, *args), name=name).start()


def _save_interrupted(
    plan: TurnPlan, question: str, tenant_id: uuid.UUID, factory: sessionmaker[Session]
) -> None:
    with span("conversation.turn_interrupted", **{TENANT_ID: str(tenant_id)}):
        try:
            # Its own session: the worker's transaction (if any) is still open.
            with tenant_transaction(factory, tenant_id) as session:
                save_interrupted_turn(session, tenant_id, plan, question)
        except Exception:
            logger.exception("query.interrupted_save_failed", extra={"tenant_id": str(tenant_id)})


def _answer_in_background(
    put: Put,
    turn: _Turn,
    plan: TurnPlan,
    question: str,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    def on_phase(phase: str) -> None:
        event = PhaseEvent.model_validate({"phase": phase, "label": PHASE_LABELS[phase]})
        put(sse("phase", event.model_dump_json()))

    try:
        embeddings = get_embeddings(settings)
        with tenant_transaction(factory, tenant_id) as session:
            response = run_turn(
                session,
                tenant_id=tenant_id,
                plan=plan,
                question=question,
                resolver=get_resolver(settings),
                embeddings=embeddings,
                llm=get_llm(settings, embeddings),
                settings=settings,
                on_phase=on_phase,
                judge=get_judge(settings),
            )
            if not turn.commit(session):
                raise _Discarded
        # Sent only after the commit: a `result` always means the turn is saved.
        put(sse("result", response.model_dump_json()))
    except Exception as exc:
        if isinstance(exc, _Discarded) or not turn.fail():
            # The client left first. A failure here is expected too: saving the late
            # answer of a new conversation collides with the one the interrupted turn
            # created. Either way the answer is rolled back.
            logger.info(
                "query.stream_answer_discarded",
                extra={
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(plan.conversation_id),
                    "error_type": None if isinstance(exc, _Discarded) else type(exc).__name__,
                },
            )
        elif isinstance(exc, AppError):  # 503/504 from the providers, 404 if deleted
            body = problem_body(exc.status, exc.title, exc.detail, exc.type)
            put(sse("error", json.dumps(body)))
        else:
            logger.exception("query.stream_failed", extra={"tenant_id": str(tenant_id)})
            body = problem_body(500, "Internal error", "An unexpected error occurred.")
            put(sse("error", json.dumps(body)))
        put(_DONE)
        return

    after_answer(put, plan, question, response, tenant_id, factory, settings)
    put(_DONE)


def after_answer(
    put: Put,
    plan: TurnPlan,
    question: str,
    response: QueryResponse,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    """Suggestions (answered turns) and the title (first turn of a new conversation),
    once the turn is saved. Keyless mode skips both. Never raises: a problem here only
    means no suggestions or the provisional title."""
    if response.message_id is None:
        return
    try:
        _suggest(put, question, response, tenant_id, factory, settings)
    except Exception:
        logger.exception("conversation.suggestions_failed", extra={"tenant_id": str(tenant_id)})
    if plan.is_new:
        try:
            _title(put, plan, question, response, tenant_id, factory, settings)
        except Exception:
            logger.exception("conversation.title_store_failed", extra={"tenant_id": str(tenant_id)})


def _suggest(
    put: Put,
    question: str,
    response: QueryResponse,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    if response.status != "answered" or response.message_id is None:
        return
    generator = get_suggestion_generator(settings)
    if generator is None:
        return
    standalone = (response.details and response.details.standalone_question) or question
    suggestions = generator.suggest(standalone, response.answer, response.citations)
    if not suggestions:
        return
    with tenant_transaction(factory, tenant_id) as session:
        stored = store_suggestions(session, tenant_id, response.message_id, suggestions)
    if stored:
        event = SuggestionsEvent(message_id=response.message_id, suggestions=suggestions)
        put(sse("suggestions", event.model_dump_json()))


def _title(
    put: Put,
    plan: TurnPlan,
    question: str,
    response: QueryResponse,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    generator = get_title_generator(settings)
    if generator is None:
        return
    title = generator.title(question, response.answer)
    if title is None or title == plan.title:
        return
    with tenant_transaction(factory, tenant_id) as session:
        stored = store_generated_title(session, tenant_id, plan, title)
    if stored:
        event = TitleEvent(conversation_id=plan.conversation_id, title=title)
        put(sse("title", event.model_dump_json()))

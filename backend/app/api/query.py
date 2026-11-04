"""Question answering over the tenant's documents, inside a conversation.

- `POST /query`: plain JSON. Answers, stores the turn and returns it.
- `POST /query/stream`: the same, as Server-Sent Events: progress phases, then ONE
  validated result. The answer text is never streamed token by token: an answer is only
  shown after its citations were checked.

Both store the turn (question + answer) atomically after a successful answer; on a
503/504 nothing is stored (see `app/conversations/service.py`).
"""

import contextvars
import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import CurrentTenant, TenantSession, get_session_factory
from app.api.problem import problem_body
from app.conversations.resolver import get_resolver
from app.conversations.service import TurnPlan, plan_turn, run_turn
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.session import tenant_transaction
from app.providers import get_embeddings
from app.providers.llm import get_llm
from app.retrieval.judge import get_judge
from app.schemas.conversations import PHASE_LABELS, ConversationEvent, PhaseEvent
from app.schemas.query import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)

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
    return run_turn(
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


@router.post(
    "/query/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Events: `conversation` (first), `phase` (0..n), then `result` "
                "(a QueryResponse) or `error` (Problem Details)."
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


def _stream_events(
    plan: TurnPlan,
    question: str,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> Iterator[str]:
    """Sync generator: Starlette iterates it in its thread pool.

    The answer is one blocking call that reports phases through a callback, and a
    generator cannot yield from inside a callback. So the answer runs in its own thread
    and puts ready-made events in a queue; this generator just forwards them. (The
    alternative, splitting the answer service into resumable steps, would complicate the
    code shared with `POST /query`.)

    If the client disconnects, Starlette stops reading this generator, but the thread
    still finishes and saves the turn: the answer shows up when the conversation is
    reloaded. That is intentional (the work was paid for), and it is the same as a
    client giving up on `POST /query` mid-request.
    """
    yield sse(
        "conversation",
        ConversationEvent(conversation_id=plan.conversation_id, title=plan.title).model_dump_json(),
    )

    events: queue.Queue[object] = queue.Queue()
    # Run with a copy of the current context, so the thread's spans belong to the
    # request's trace (and `trace_id` in the details and errors is the request's).
    context = contextvars.copy_context()
    worker = threading.Thread(
        target=context.run,
        args=(_answer_in_background, events, plan, question, tenant_id, factory, settings),
        name="query-stream",
    )
    worker.start()
    while True:
        try:
            item = events.get(timeout=HEARTBEAT_S)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if item is _DONE:
            return
        assert isinstance(item, str)
        yield item


def _answer_in_background(
    events: queue.Queue[object],
    plan: TurnPlan,
    question: str,
    tenant_id: uuid.UUID,
    factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    def on_phase(phase: str) -> None:
        event = PhaseEvent.model_validate({"phase": phase, "label": PHASE_LABELS[phase]})
        events.put(sse("phase", event.model_dump_json()))

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
        # Sent only after the commit: a `result` always means the turn is saved.
        events.put(sse("result", response.model_dump_json()))
    except AppError as exc:  # 503/504 from the providers, 404 if deleted meanwhile
        body = problem_body(exc.status, exc.title, exc.detail, exc.type)
        events.put(sse("error", json.dumps(body)))
    except Exception:
        logger.exception("query.stream_failed", extra={"tenant_id": str(tenant_id)})
        body = problem_body(500, "Internal error", "An unexpected error occurred.")
        events.put(sse("error", json.dumps(body)))
    finally:
        events.put(_DONE)

"""Small helpers for manual spans, and the attribute names used across the code.

Only the OpenTelemetry *API* is used here: without a configured SDK every span is a
no-op, so modules can add spans and attributes freely (and tests run without any
collector). Exporting is set up once per process in `app/core/telemetry.py`.

Rule: attributes carry ids, counts, sizes, timings and model names. Never document
content, prompts, answers or keys.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanContext, Status, StatusCode, TraceFlags

_tracer = trace.get_tracer("rutaviva")

# --- Attribute names (one place, so traces stay consistent) -----------------------------
TENANT_ID = "tenant.id"
DOCUMENT_ID = "document.id"
DOCUMENT_PAGES = "document.pages"
CHUNKS_COUNT = "chunks.count"
EMBEDDING_MODEL = "embedding.model"
EMBEDDING_BATCH_SIZE = "embedding.batch_size"
TASK_RETRIES = "task.retries"
TASK_ATTEMPT = "task.attempt"
FAILURE_CODE = "document.failure_code"

RETRIEVAL_K = "retrieval.k"
RETRIEVAL_VEC_HITS = "retrieval.vec_hits"
RETRIEVAL_FTS_HITS = "retrieval.fts_hits"
RETRIEVAL_RESULTS = "retrieval.results"

# GenAI semantic conventions where they exist; `llm.*` for what they do not cover.
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
LLM_TOKENS_ESTIMATED = "llm.tokens.estimated"
LLM_LATENCY_MS = "llm.latency_ms"
LLM_RETRIES = "llm.retries"
LLM_VALIDATION_ERROR = "llm.validation_error"

ANSWER_INSUFFICIENT = "answer.insufficient"
ANSWER_CONFIDENCE = "answer.confidence"
ANSWER_CITATIONS = "answer.citations"
PROMPT_INJECTION_FLAGGED = "prompt_injection.flagged"


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a child span of the current context.

    Attribute keys use the constants above; pass them with `**{ATTR: value}`. `None`
    values are skipped. Exceptions are recorded and the span is marked as an error,
    then re-raised.
    """
    with _tracer.start_as_current_span(name, record_exception=True) as current:
        set_attributes(current, **attributes)
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def set_attributes(target: Span | None = None, **attributes: Any) -> None:
    """Set attributes on `target` (default: the current span), skipping `None`."""
    target = target or trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            target.set_attribute(key, value)


def current_span_id() -> str | None:
    """Hex span id of the current span, or None outside a recorded trace."""
    context = trace.get_current_span().get_span_context()
    return format(context.span_id, "016x") if context.is_valid else None


def link_to(trace_id: str | None, span_id: str | None, **attributes: Any) -> None:
    """Add a link from the current span to a span of another trace (both ids in hex).

    Used to connect a query to the ingestion of the documents it retrieved: they are
    different traces (a question is not caused by an upload), but navigable in Jaeger.
    Invalid or missing ids are ignored.
    """
    if not trace_id or not span_id:
        return
    try:
        context = SpanContext(
            trace_id=int(trace_id, 16),
            span_id=int(span_id, 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    except ValueError:
        return
    if context.is_valid:
        trace.get_current_span().add_link(context, {k: v for k, v in attributes.items() if v})


def current_trace_id() -> str | None:
    """Hex trace id of the current span, or None outside a recorded trace."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None

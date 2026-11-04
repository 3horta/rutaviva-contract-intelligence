"""Small helpers for manual spans, and the attribute names used across the code.

Only the OpenTelemetry *API* is used here: without a configured SDK every span is a
no-op, so modules can add spans and attributes freely (and tests run without any
collector). Exporting is set up once per process in `app/core/telemetry.py`.

Rule: attributes carry ids, counts, sizes, timings and model names. Never document
content, prompts, answers or keys.

GenAI spans follow the OpenTelemetry GenAI semantic conventions (status: development;
github.com/open-telemetry/semantic-conventions-genai), so any GenAI-aware backend reads
them without configuration (ADR-018):

- `invoke_workflow contract_qa` (INTERNAL): one answer. A fixed workflow, not an agent:
  the code, not a model, decides the steps, so there is no `invoke_agent` span.
- `embeddings {model}` (CLIENT): one embedding call (the question, or a batch of chunks).
- `retrieval` (INTERNAL): the hybrid search; ids and scores of what it returned.
- `chat {model}` (CLIENT): one LLM call; `gen_ai.prompt.name` says which prompt
  template (`answer`, `relevance_judge`, `resolve_follow_up`, ...).
- `tools/call {tool}` (`execute_tool`): an MCP tool call, emitted by the MCP SDK.

Content attributes the conventions define as opt-in (`gen_ai.input.messages`,
`gen_ai.output.messages`, `gen_ai.retrieval.query.text`) are never recorded.
"""

import contextvars
import json
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanContext, SpanKind, Status, StatusCode, TraceFlags

from app.core import metrics

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
# Relevance judge (real LLM only): chunks it received, chunks kept, and whether it failed
# (failure keeps every chunk).
JUDGE_CANDIDATES = "retrieval.judge.candidates"
JUDGE_KEPT = "retrieval.judge.kept"
JUDGE_FAILED = "retrieval.judge.failed"
# Cross-encoder reranking of the fused candidates: model, how many it scored, how long it
# took, and whether it failed (failure keeps the RRF order).
RERANK_MODEL = "retrieval.rerank.model"
RERANK_CANDIDATES = "retrieval.rerank.candidates"
RERANK_LATENCY_MS = "retrieval.rerank.latency_ms"
RERANK_FAILED = "retrieval.rerank.failed"

# GenAI semantic conventions where they exist; `llm.*` for what they do not cover.
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_OUTPUT_TYPE = "gen_ai.output.type"
GEN_AI_PROMPT_NAME = "gen_ai.prompt.name"
GEN_AI_WORKFLOW_NAME = "gen_ai.workflow.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_EMBEDDINGS_DIMENSION = "gen_ai.embeddings.dimension.count"
GEN_AI_RETRIEVAL_TOP_K = "gen_ai.retrieval.top_k"
GEN_AI_RETRIEVAL_DOCUMENTS = "gen_ai.retrieval.documents"
GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ERROR_TYPE = "error.type"
LLM_TOKENS_ESTIMATED = "llm.tokens.estimated"
LLM_LATENCY_MS = "llm.latency_ms"
LLM_RETRIES = "llm.retries"
LLM_VALIDATION_ERROR = "llm.validation_error"
LLM_CONTENT_FILTERED = "llm.content_filtered"

ANSWER_INSUFFICIENT = "answer.insufficient"
ANSWER_CONFIDENCE = "answer.confidence"
ANSWER_CITATIONS = "answer.citations"
PROMPT_INJECTION_FLAGGED = "prompt_injection.flagged"


# `gen_ai.operation.name` values used here.
OP_CHAT = "chat"
OP_EMBEDDINGS = "embeddings"
OP_RETRIEVAL = "retrieval"
OP_INVOKE_WORKFLOW = "invoke_workflow"

# The answer pipeline, as `gen_ai.workflow.name`.
WORKFLOW_CONTRACT_QA = "contract_qa"

# `gen_ai.provider.name`: Gemini through its AI Studio endpoint is the convention's
# `gcp.gemini`. The keyless providers are not services, so they get custom values.
PROVIDER_GEMINI = "gcp.gemini"
PROVIDER_FAKE = "rutaviva.fake"
PROVIDER_LOCAL = "rutaviva.local"


@contextmanager
def span(name: str, *, kind: SpanKind = SpanKind.INTERNAL, **attributes: Any) -> Iterator[Span]:
    """Start a child span of the current context.

    Attribute keys use the constants above; pass them with `**{ATTR: value}`. `None`
    values are skipped. Exceptions are recorded, the span is marked as an error with
    `error.type` (see `_error_type`), then re-raised.
    """
    with _tracer.start_as_current_span(name, kind=kind, record_exception=True) as current:
        set_attributes(current, **attributes)
        try:
            yield current
        except Exception as exc:
            error_type = _error_type(exc)
            current.set_attribute(ERROR_TYPE, error_type)
            current.set_status(Status(StatusCode.ERROR, error_type))
            raise


def _error_type(exc: BaseException) -> str:
    """The class of the original error: when an error was translated (`raise
    GatewayTimeout(...) from ProviderTimeoutError`), the provider's one, which is what the
    GenAI conventions ask for."""
    cause = exc.__cause__
    return type(cause if isinstance(cause, Exception) else exc).__name__


@dataclass(frozen=True)
class ModelRef:
    """A model id of this app, as the GenAI conventions name it."""

    provider: str
    name: str
    dimensions: int | None = None


def model_ref(model_id: str) -> ModelRef:
    """`gemini/gemini-3.5-flash` -> (`gcp.gemini`, `gemini-3.5-flash`);
    `bge-base-en-v1.5@768` -> (`rutaviva.local`, `bge-base-en-v1.5`, 768). Embedding ids
    carry their dimensions after `@` (they are part of the index's identity)."""
    name, _, dims = model_id.partition("@")
    if name.startswith("gemini/"):
        provider, name = PROVIDER_GEMINI, name.removeprefix("gemini/")
    elif name.startswith("gemini-"):
        provider = PROVIDER_GEMINI
    elif name.startswith("fake-"):
        provider = PROVIDER_FAKE
    else:
        provider = PROVIDER_LOCAL
    return ModelRef(provider, name, int(dims) if dims.isdigit() else None)


@dataclass
class _ModelCall:
    """The model call in progress, for `record_usage` (set by `model_span`)."""

    metric_attributes: dict[str, Any]
    prompt_name: str | None


_model_call: contextvars.ContextVar[_ModelCall | None] = contextvars.ContextVar(
    "model_call", default=None
)


@contextmanager
def model_span(operation: str, model_id: str, **attributes: Any) -> Iterator[Span]:
    """A CLIENT span for one model call: `{operation} {model}` (`chat`, `embeddings`),
    with `gen_ai.operation.name`, `gen_ai.provider.name` and `gen_ai.request.model`.

    It also records the call's duration as the GenAI metric of its operation (with
    `error.type` when it failed). Tokens are recorded by `record_usage`, called inside
    the block once the provider has answered."""
    model = model_ref(model_id)
    genai = {
        GEN_AI_OPERATION_NAME: operation,
        GEN_AI_PROVIDER_NAME: model.provider,
        GEN_AI_REQUEST_MODEL: model.name,
    }
    call = _ModelCall(metric_attributes=dict(genai), prompt_name=attributes.get(GEN_AI_PROMPT_NAME))
    token = _model_call.set(call)
    started = time.perf_counter()
    error_type: str | None = None
    try:
        with span(
            f"{operation} {model.name}",
            kind=SpanKind.CLIENT,
            **genai,
            **{GEN_AI_EMBEDDINGS_DIMENSION: model.dimensions},
            **attributes,
        ) as current:
            yield current
    except Exception as exc:
        error_type = _error_type(exc)
        raise
    finally:
        _model_call.reset(token)
        metrics.record_model_duration(
            operation,
            time.perf_counter() - started,
            {**call.metric_attributes, ERROR_TYPE: error_type},
        )


def record_usage(
    target: Span,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    response_model: str | None = None,
    **attributes: Any,
) -> None:
    """Token usage of the current model call: on its span (`gen_ai.usage.*`,
    `gen_ai.response.model`, plus `attributes`) and in the token metrics. Call it inside
    `model_span`; outside one, only the span is updated."""
    response = model_ref(response_model).name if response_model else None
    set_attributes(
        target,
        **{
            GEN_AI_RESPONSE_MODEL: response,
            GEN_AI_INPUT_TOKENS: input_tokens,
            GEN_AI_OUTPUT_TOKENS: output_tokens,
        },
        **attributes,
    )
    call = _model_call.get()
    if call is None:
        return
    if response:
        call.metric_attributes[GEN_AI_RESPONSE_MODEL] = response
    metrics.record_tokens(
        input_tokens, output_tokens, call.metric_attributes, prompt_name=call.prompt_name
    )


def retrieved_documents(items: Sequence[tuple[str, float]]) -> str:
    """`gen_ai.retrieval.documents` as a JSON string (allowed on spans): ids and scores
    only, never text."""
    return json.dumps([{"id": item_id, "score": round(score, 6)} for item_id, score in items])


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

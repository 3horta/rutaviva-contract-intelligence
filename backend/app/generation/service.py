"""Answer a question from the tenant's documents:
embed -> retrieve -> judge relevance -> generate -> validate.

The relevance judge only runs with a real LLM (see `app/retrieval/judge.py`): it keeps
the retrieved chunks that are actually relevant, and on any failure keeps them all.

"Not enough information" is decided in exactly these places, never by a score threshold:
  (a) retrieval returns nothing: the LLM is not even called;
  (b) every retrieved chunk is flagged as prompt injection (see below): same;
  (c) the LLM itself declares `insufficient_evidence`;
  (d) its output fails validation on every attempt (we never show an unchecked answer);
  (e) the provider's safety filter blocks the prompt or the reply: reported with the
      warning `content_filtered` and reason `llm_declared`, and never retried (the same
      input is blocked again). It is an answer, not a 503.
In all of them the answer is `INSUFFICIENT_EVIDENCE_MESSAGE` with status
`insufficient_evidence`, confidence `low` and no citations.

Attempts: up to `1 + llm_max_retries`, shared by:
- invalid output: retried, with the validation error fed back to the model;
- timeouts (`ProviderTimeoutError`): retried while attempts remain;
- other transient provider errors (429, 5xx): retried at most ONCE, after a short wait
  (the provider's Retry-After if it sends one, capped), because an overloaded provider
  rarely recovers within the same request.
Provider errors that survive become HTTP errors: timeout -> 504 `GatewayTimeout`; any
other provider error -> 503 `ServiceUnavailable`. A permanent error is not retried.

Progress: an optional `on_phase` callback is called with "searching" before retrieval,
"drafting" before each LLM call and "validating" before each validation, so the stream
endpoint can show progress. It never receives any text of the answer.

Every response carries `details` (`AnswerDetails`): which chunks were retrieved and by
which branch, attempts, why it was insufficient, latency, model and trace id. Ids and
numbers only, never chunk content.

Confidence = min(confidence given by the LLM, retrieval signal). The retrieval signal is
`high` only when the top chunk was found by BOTH the vector and the full-text branch
(semantically close AND sharing the question's words); otherwise `medium`. So a single
branch match can never produce a `high` answer.

Untrusted content (ADR-013):
- Chunks flagged as prompt injection at indexing are removed from the LLM context (for
  every provider) but still listed in `details.retrieved` with `flagged=true`. If any
  was removed, the answer carries the warning `prompt_injection`.
- When the quotes come from two or more documents that state different values for the
  same kind of quantity (`guardrails.sources_disagree`), the answer keeps both
  citations, its confidence is capped at `medium` and it carries `sources_disagree`.
"""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry.trace import Span
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    ContentFilteredError,
    GatewayTimeout,
    PermanentProviderError,
    ProviderError,
    ProviderTimeoutError,
    ServiceUnavailable,
    TransientProviderError,
)
from app.core.tracing import (
    ANSWER_CITATIONS,
    ANSWER_CONFIDENCE,
    ANSWER_INSUFFICIENT,
    DOCUMENT_ID,
    EMBEDDING_MODEL,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_OUTPUT_TYPE,
    GEN_AI_PROMPT_NAME,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RETRIEVAL_DOCUMENTS,
    GEN_AI_RETRIEVAL_TOP_K,
    GEN_AI_WORKFLOW_NAME,
    LLM_CONTENT_FILTERED,
    LLM_LATENCY_MS,
    LLM_RETRIES,
    LLM_TOKENS_ESTIMATED,
    LLM_VALIDATION_ERROR,
    OP_CHAT,
    OP_EMBEDDINGS,
    OP_INVOKE_WORKFLOW,
    OP_RETRIEVAL,
    PROMPT_INJECTION_FLAGGED,
    TENANT_ID,
    WORKFLOW_CONTRACT_QA,
    current_trace_id,
    link_to,
    model_ref,
    model_span,
    retrieved_documents,
    set_attributes,
    span,
)
from app.generation.guardrails import sources_disagree
from app.generation.validate import ValidatedAnswer, ValidationFailure, validate_answer
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import ContextChunk, LLMProvider, PromptInput
from app.providers.llm.traced import PROMPT_ANSWER
from app.retrieval.hybrid import RetrievedChunk, search
from app.retrieval.judge import KeepAll, RelevanceJudge
from app.schemas.query import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    WARNING_PROMPT_INJECTION,
    WARNING_SOURCES_DISAGREE,
    AnswerDetails,
    Citation,
    Confidence,
    FoundBy,
    InsufficientReason,
    LLMAnswer,
    QueryResponse,
    RetrievedChunkOut,
)

logger = logging.getLogger(__name__)

_CONFIDENCE_ORDER: list[Confidence] = ["low", "medium", "high"]

# Non-timeout transient errors (429, 5xx): one retry after a short wait.
MAX_TRANSIENT_RETRIES = 1
TRANSIENT_RETRY_WAIT_S = 1.0
MAX_RETRY_AFTER_S = 5.0
_sleep = time.sleep  # replaced in tests

# Called with "searching", "drafting" or "validating".
PhaseCallback = Callable[[str], None]

# Warning added when the provider's safety filter blocked the answer.
CONTENT_FILTERED_WARNING = "content_filtered"


@dataclass(frozen=True)
class AnswerOutcome:
    """The response (with `details`) plus the token usage, which the API stores on the
    assistant message but does not return."""

    response: QueryResponse
    tokens_in: int  # summed over every LLM attempt: each one is paid for
    tokens_out: int


@dataclass(frozen=True)
class Generation:
    answer: LLMAnswer | None  # None: no attempt produced a valid answer
    attempts: int
    tokens_in: int
    tokens_out: int
    content_filtered: bool = False  # the provider's safety filter blocked it


@dataclass(frozen=True)
class Retrieval:
    """The chunks the LLM receives, and how many retrieval found before the judge."""

    chunks: list[RetrievedChunk]
    candidates: int
    judged: bool


def answer_question(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
    on_phase: PhaseCallback | None = None,
    judge: RelevanceJudge | None = None,
) -> QueryResponse:
    """The validated answer, with `details` filled. `conversation_id` and `message_id`
    stay null: persisting the turn is the caller's job."""
    return answer_with_usage(
        question,
        tenant_id=tenant_id,
        session=session,
        embeddings=embeddings,
        llm=llm,
        settings=settings,
        on_phase=on_phase,
        judge=judge,
    ).response


def answer_with_usage(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
    on_phase: PhaseCallback | None = None,
    judge: RelevanceJudge | None = None,
    conversation_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    """`answer_question` plus the token usage of the LLM calls. `judge` defaults to none
    (keyless mode: the retrieved chunks go to the LLM unchanged). `conversation_id` only
    labels the trace (`gen_ai.conversation.id`); nothing is stored here."""
    started = time.perf_counter()
    notify = on_phase or _no_phase
    with span(
        f"{OP_INVOKE_WORKFLOW} {WORKFLOW_CONTRACT_QA}",
        **{
            GEN_AI_OPERATION_NAME: OP_INVOKE_WORKFLOW,
            GEN_AI_WORKFLOW_NAME: WORKFLOW_CONTRACT_QA,
            GEN_AI_CONVERSATION_ID: str(conversation_id) if conversation_id else None,
            TENANT_ID: str(tenant_id),
        },
    ) as answer_span:
        notify("searching")
        candidates = retrieve(
            question, tenant_id=tenant_id, session=session, embeddings=embeddings, settings=settings
        )
        selection = (judge or KeepAll()).judge(question, candidates)
        retrieval = Retrieval(selection.chunks, len(candidates), selection.judged)
        usable, warnings = prepare_context(answer_span, retrieval)
        generation = generate_validated(question, usable, llm, settings, notify) if usable else None
        return conclude(
            answer_span, tenant_id, retrieval, usable, warnings, generation, llm, started
        )


def retrieve(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    settings: Settings,
) -> list[RetrievedChunk]:
    """Embed the question and run the hybrid search (with the reranker), as GenAI
    `embeddings` and `retrieval` spans. Shared by the workflow and the agent's
    `search_contracts` tool. Provider errors become 503/504."""
    with model_span(OP_EMBEDDINGS, embeddings.model_id, **{EMBEDDING_MODEL: embeddings.model_id}):
        try:
            query_vector = embeddings.embed_query(question)
        except ProviderError as exc:
            raise _to_app_error(exc) from exc

    with span(
        OP_RETRIEVAL,
        **{GEN_AI_OPERATION_NAME: OP_RETRIEVAL, GEN_AI_RETRIEVAL_TOP_K: settings.retrieval_k},
    ) as retrieval_span:
        candidates = search(
            session,
            tenant_id=tenant_id,
            query_vector=query_vector,
            question=question,
            embedding_model=embeddings.model_id,
            settings=settings,
        )
        # Ids and scores only (the reranker's score when it ran, else the RRF one).
        set_attributes(
            retrieval_span,
            **{
                GEN_AI_RETRIEVAL_DOCUMENTS: retrieved_documents(
                    [
                        (str(c.chunk_id), c.rerank_score if c.rerank_score is not None else c.score)
                        for c in candidates
                    ]
                )
            },
        )
    return candidates


def prepare_context(
    answer_span: Span, retrieval: Retrieval
) -> tuple[list[RetrievedChunk], list[str]]:
    """Link the answer to the ingestion traces of its sources and drop flagged chunks:
    the chunks the LLM may see, and the warnings so far (ADR-013)."""
    chunks = retrieval.chunks
    _link_sources(chunks)
    # Flagged chunks never reach the LLM; `chunks` keeps them for `details`.
    usable = [chunk for chunk in chunks if not chunk.flagged_injection]
    flagged = len(chunks) - len(usable)
    set_attributes(answer_span, **{PROMPT_INJECTION_FLAGGED: flagged})
    return usable, [WARNING_PROMPT_INJECTION] if flagged else []


def conclude(
    answer_span: Span,
    tenant_id: uuid.UUID,
    retrieval: Retrieval,
    usable: list[RetrievedChunk],
    warnings: list[str],
    generation: Generation | None,
    llm: LLMProvider,
    started: float,
) -> AnswerOutcome:
    """The response from what was retrieved and what the LLM produced (None when it was
    not called). Every "not enough information" reason is decided here."""
    chunks = retrieval.chunks
    flagged = len(chunks) - len(usable)

    def insufficient(reason: InsufficientReason, generation: Generation) -> AnswerOutcome:
        return _insufficient(
            answer_span, tenant_id, reason, retrieval, generation, llm, started, warnings
        )

    nothing = Generation(answer=None, attempts=0, tokens_in=0, tokens_out=0)
    if not chunks:
        return insufficient("no_results", nothing)
    if not usable or generation is None:
        return insufficient("all_flagged", nothing)

    answer, attempts = generation.answer, generation.attempts
    if generation.content_filtered:
        return insufficient("llm_declared", generation)
    if answer is None:
        return insufficient("validation_failed", generation)
    if answer.insufficient_evidence:
        return insufficient("llm_declared", generation)

    citations = _enrich_citations(answer, usable)
    confidence = _combined_confidence(answer.confidence, usable)
    if sources_disagree(citations):
        # Degrade, don't pick: both sources stay cited.
        confidence = min(confidence, "medium", key=_CONFIDENCE_ORDER.index)
        warnings.append(WARNING_SOURCES_DISAGREE)
    set_attributes(
        answer_span,
        **{
            ANSWER_INSUFFICIENT: False,
            ANSWER_CONFIDENCE: confidence,
            ANSWER_CITATIONS: len(citations),
        },
    )
    logger.info(
        "query.answered",
        extra={
            "tenant_id": str(tenant_id),
            "chunks": len(chunks),
            "flagged_chunks": flagged,
            "citations": len(citations),
            "attempts": attempts,
            "confidence": confidence,
            "warnings": warnings,
        },
    )
    response = QueryResponse(
        conversation_id=None,
        answer=answer.answer,
        status="answered",
        confidence=confidence,
        citations=citations,
        warnings=warnings,
        details=_details(retrieval, attempts, None, llm, started),
    )
    return AnswerOutcome(response, generation.tokens_in, generation.tokens_out)


def _no_phase(phase: str) -> None:
    pass


def generate_validated(
    question: str,
    chunks: list[RetrievedChunk],
    llm: LLMProvider,
    settings: Settings,
    notify: PhaseCallback,
) -> Generation:
    """Call the LLM until its output validates. Returns the answer (None if no attempt
    validated), the attempts made and the tokens used. Raises `GatewayTimeout` /
    `ServiceUnavailable` when the provider keeps failing."""
    context = [
        ContextChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            filename=chunk.filename,
            page=chunk.page,
            text=chunk.content,
            flagged_injection=chunk.flagged_injection,
        )
        for chunk in chunks
    ]
    max_attempts = 1 + settings.llm_max_retries
    feedback: str | None = None
    transient_retries = 0
    tokens_in = tokens_out = 0

    for attempt in range(1, max_attempts + 1):
        is_last = attempt == max_attempts
        prompt = PromptInput(question=question, chunks=context, feedback=feedback, attempt=attempt)
        notify("drafting")
        with model_span(
            OP_CHAT,
            llm.model_id,
            **{
                GEN_AI_PROMPT_NAME: PROMPT_ANSWER,
                GEN_AI_OUTPUT_TYPE: "json",
                LLM_RETRIES: attempt - 1,
            },
        ) as llm_span:
            try:
                result = llm.generate(prompt, timeout_s=settings.llm_timeout_s)
            except ProviderTimeoutError as exc:
                # Timeouts: retried while attempts remain.
                if is_last:
                    raise _to_app_error(exc) from exc  # the span records it as an error
                llm_span.record_exception(exc)
                continue
            except TransientProviderError as exc:
                # 429 / 5xx: one retry after a short wait, then give up.
                if is_last or transient_retries >= MAX_TRANSIENT_RETRIES:
                    raise _to_app_error(exc) from exc
                transient_retries += 1
                llm_span.record_exception(exc)
                _sleep(min(exc.retry_after_s or TRANSIENT_RETRY_WAIT_S, MAX_RETRY_AFTER_S))
                continue
            except PermanentProviderError as exc:
                raise _to_app_error(exc) from exc
            except ContentFilteredError:
                # Blocked by the provider's safety filter: an outcome, not a failure.
                # Not retried: the same question and documents are blocked again.
                set_attributes(llm_span, **{LLM_CONTENT_FILTERED: True})
                logger.warning("llm.content_filtered", extra={"attempt": attempt})
                return Generation(None, attempt, tokens_in, tokens_out, content_filtered=True)

            tokens_in += result.input_tokens
            tokens_out += result.output_tokens
            notify("validating")
            outcome = validate_answer(result.text, context)
            set_attributes(
                llm_span,
                **{
                    GEN_AI_RESPONSE_MODEL: model_ref(result.model).name,
                    GEN_AI_INPUT_TOKENS: result.input_tokens,
                    GEN_AI_OUTPUT_TOKENS: result.output_tokens,
                    LLM_TOKENS_ESTIMATED: result.tokens_estimated,
                    LLM_LATENCY_MS: result.latency_ms,
                    LLM_VALIDATION_ERROR: (
                        outcome.reason if isinstance(outcome, ValidationFailure) else None
                    ),
                },
            )
        if isinstance(outcome, ValidatedAnswer):
            return Generation(outcome.answer, attempt, tokens_in, tokens_out)
        logger.warning("llm.invalid_output", extra={"attempt": attempt, "reason": outcome.reason})
        feedback = outcome.detail

    return Generation(None, max_attempts, tokens_in, tokens_out)


def _link_sources(chunks: list[RetrievedChunk]) -> None:
    """Link the query span to the ingestion trace of every retrieved document, so in
    Jaeger one can jump from a question to how its sources were indexed. The ids come
    with the search results (no extra query)."""
    seen: set[uuid.UUID] = set()
    for chunk in chunks:
        if chunk.document_id not in seen:
            seen.add(chunk.document_id)
            link_to(
                chunk.ingest_trace_id, chunk.ingest_span_id, **{DOCUMENT_ID: str(chunk.document_id)}
            )


def _to_app_error(exc: ProviderError) -> GatewayTimeout | ServiceUnavailable:
    # The detail is generic on purpose: provider messages may include request details.
    if isinstance(exc, ProviderTimeoutError):
        return GatewayTimeout("The language or embedding provider did not answer in time.")
    return ServiceUnavailable("The language or embedding provider is unavailable.")


def _enrich_citations(answer: LLMAnswer, chunks: list[RetrievedChunk]) -> list[Citation]:
    """The LLM only gives chunk_id + quote; document, filename and page come from the
    retrieved rows (validation already guaranteed every chunk_id is among them)."""
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    citations: list[Citation] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    for item in answer.citations:
        if (item.chunk_id, item.quote) in seen:
            continue
        seen.add((item.chunk_id, item.quote))
        chunk = by_id[item.chunk_id]
        citations.append(
            Citation(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                document_filename=chunk.filename,
                page=chunk.page,
                quote=item.quote,
            )
        )
    return citations


def _combined_confidence(llm_confidence: Confidence, chunks: list[RetrievedChunk]) -> Confidence:
    retrieval_signal: Confidence = "high" if chunks[0].found_by_both else "medium"
    return min(llm_confidence, retrieval_signal, key=_CONFIDENCE_ORDER.index)


def _found_by(chunk: RetrievedChunk) -> FoundBy:
    if chunk.found_by_both:
        return "both"
    return "vector" if chunk.vec_rank is not None else "full_text"


def _details(
    retrieval: Retrieval,
    attempts: int,
    reason: InsufficientReason | None,
    llm: LLMProvider,
    started: float,
) -> AnswerDetails:
    # Deliberately no chunk content: details are stored and shown, the text is not.
    chunks = retrieval.chunks
    return AnswerDetails(
        retrieved=[
            RetrievedChunkOut(
                document_id=chunk.document_id,
                document_filename=chunk.filename,
                page=chunk.page,
                chunk_id=chunk.chunk_id,
                score=chunk.score,
                found_by=_found_by(chunk),
                flagged=chunk.flagged_injection,
            )
            for chunk in chunks
        ],
        vector_hits=sum(1 for chunk in chunks if chunk.vec_rank is not None),
        full_text_hits=sum(1 for chunk in chunks if chunk.fts_rank is not None),
        attempts=attempts,
        insufficient_reason=reason,
        latency_ms=round((time.perf_counter() - started) * 1000),
        llm_model=llm.model_id,
        trace_id=current_trace_id(),
        judged=retrieval.judged,
        judge_candidates=retrieval.candidates if retrieval.judged else None,
    )


def _insufficient(
    answer_span: Span,
    tenant_id: uuid.UUID,
    reason: InsufficientReason,
    retrieval: Retrieval,
    generation: Generation,
    llm: LLMProvider,
    started: float,
    warnings: list[str],
) -> AnswerOutcome:
    attempts = generation.attempts
    set_attributes(
        answer_span,
        **{ANSWER_INSUFFICIENT: True, ANSWER_CONFIDENCE: "low", ANSWER_CITATIONS: 0},
    )
    # The injection warning (if any) plus the provider's content filter, deduplicated.
    if generation.content_filtered and CONTENT_FILTERED_WARNING not in warnings:
        warnings = [*warnings, CONTENT_FILTERED_WARNING]
    logger.info(
        "query.insufficient",
        extra={
            "tenant_id": str(tenant_id),
            "reason": reason,
            "attempts": attempts,
            "warnings": warnings,
        },
    )
    response = QueryResponse(
        conversation_id=None,
        answer=INSUFFICIENT_EVIDENCE_MESSAGE,
        status="insufficient_evidence",
        confidence="low",
        citations=[],
        warnings=warnings,
        details=_details(retrieval, attempts, reason, llm, started),
    )
    return AnswerOutcome(response, generation.tokens_in, generation.tokens_out)

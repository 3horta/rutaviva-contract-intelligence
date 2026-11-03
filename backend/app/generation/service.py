"""Answer a question from the tenant's documents: embed -> retrieve -> generate -> validate.

"Not enough information" is decided in exactly three places, never by a score threshold:
  (a) retrieval returns nothing: the LLM is not even called;
  (b) the LLM itself declares `insufficient_evidence`;
  (c) its output fails validation on every attempt (we never show an unchecked answer).
In all three the answer is `INSUFFICIENT_EVIDENCE_MESSAGE` with status
`insufficient_evidence`, confidence `low` and no citations.

Attempts: up to `1 + llm_max_retries`, shared by:
- invalid output: retried, with the validation error fed back to the model;
- timeouts (`ProviderTimeoutError`): retried while attempts remain;
- other transient provider errors (429, 5xx): retried at most ONCE, after a short wait
  (the provider's Retry-After if it sends one, capped), because an overloaded provider
  rarely recovers within the same request.
Provider errors that survive become HTTP errors: timeout -> 504 `GatewayTimeout`; any
other provider error -> 503 `ServiceUnavailable`. A permanent error is not retried.

Confidence = min(confidence given by the LLM, retrieval signal). The retrieval signal is
`high` only when the top chunk was found by BOTH the vector and the full-text branch
(semantically close AND sharing the question's words); otherwise `medium`. So a single
branch match can never produce a `high` answer.
"""

import logging
import time
import uuid

from opentelemetry.trace import Span
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
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
    EMBEDDING_MODEL,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_REQUEST_MODEL,
    LLM_LATENCY_MS,
    LLM_RETRIES,
    LLM_TOKENS_ESTIMATED,
    LLM_VALIDATION_ERROR,
    TENANT_ID,
    set_attributes,
    span,
)
from app.generation.validate import ValidatedAnswer, ValidationFailure, validate_answer
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import ContextChunk, LLMProvider, PromptInput
from app.retrieval.hybrid import RetrievedChunk, search
from app.schemas.query import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    Citation,
    Confidence,
    LLMAnswer,
    QueryResponse,
)

logger = logging.getLogger(__name__)

_CONFIDENCE_ORDER: list[Confidence] = ["low", "medium", "high"]

# Non-timeout transient errors (429, 5xx): one retry after a short wait.
MAX_TRANSIENT_RETRIES = 1
TRANSIENT_RETRY_WAIT_S = 1.0
MAX_RETRY_AFTER_S = 5.0
_sleep = time.sleep  # replaced in tests


def answer_question(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
) -> QueryResponse:
    with span("query.answer", **{TENANT_ID: str(tenant_id)}) as answer_span:
        with span("query.embed", **{EMBEDDING_MODEL: embeddings.model_id}):
            try:
                query_vector = embeddings.embed_query(question)
            except ProviderError as exc:
                raise _to_app_error(exc) from exc

        with span("query.retrieve"):
            chunks = search(
                session,
                tenant_id=tenant_id,
                query_vector=query_vector,
                question=question,
                embedding_model=embeddings.model_id,
                settings=settings,
            )

        if not chunks:
            return _insufficient(answer_span, tenant_id, reason="no_results", attempts=0)

        answer, attempts = _generate_validated(question, chunks, llm, settings)
        if answer is None:
            return _insufficient(
                answer_span, tenant_id, reason="validation_failed", attempts=attempts
            )
        if answer.insufficient_evidence:
            return _insufficient(answer_span, tenant_id, reason="llm_declared", attempts=attempts)

        citations = _enrich_citations(answer, chunks)
        confidence = _combined_confidence(answer.confidence, chunks)
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
                "citations": len(citations),
                "attempts": attempts,
                "confidence": confidence,
            },
        )
        return QueryResponse(
            conversation_id=None,
            answer=answer.answer,
            status="answered",
            confidence=confidence,
            citations=citations,
            warnings=[],
        )


def _generate_validated(
    question: str, chunks: list[RetrievedChunk], llm: LLMProvider, settings: Settings
) -> tuple[LLMAnswer | None, int]:
    """Call the LLM until its output validates. Returns (answer or None, attempts made).
    Raises `GatewayTimeout` / `ServiceUnavailable` when the provider keeps failing."""
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

    for attempt in range(1, max_attempts + 1):
        is_last = attempt == max_attempts
        prompt = PromptInput(question=question, chunks=context, feedback=feedback, attempt=attempt)
        with span(
            "llm.generate", **{GEN_AI_REQUEST_MODEL: llm.model_id, LLM_RETRIES: attempt - 1}
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

            outcome = validate_answer(result.text, context)
            set_attributes(
                llm_span,
                **{
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
            return outcome.answer, attempt
        logger.warning("llm.invalid_output", extra={"attempt": attempt, "reason": outcome.reason})
        feedback = outcome.detail

    return None, max_attempts


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


def _insufficient(
    answer_span: Span, tenant_id: uuid.UUID, *, reason: str, attempts: int
) -> QueryResponse:
    set_attributes(
        answer_span,
        **{ANSWER_INSUFFICIENT: True, ANSWER_CONFIDENCE: "low", ANSWER_CITATIONS: 0},
    )
    logger.info(
        "query.insufficient",
        extra={"tenant_id": str(tenant_id), "reason": reason, "attempts": attempts},
    )
    return QueryResponse(
        conversation_id=None,
        answer=INSUFFICIENT_EVIDENCE_MESSAGE,
        status="insufficient_evidence",
        confidence="low",
        citations=[],
        warnings=[],
    )

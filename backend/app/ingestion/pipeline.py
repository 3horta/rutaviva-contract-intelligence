"""Ingestion of one document: acquire it, extract, chunk, embed and store.

Pure logic with its dependencies passed in (`IngestionDeps`), so tests run it with an
in-memory storage, a scripted extractor and fake embeddings. The Celery task in
`app/worker/tasks.py` is a thin wrapper that adds retries.

State machine (see ADR-004 and ADR-005):
- **Acquire**: one atomic UPDATE moves `pending → processing`, or takes over a
  `processing` document whose lease (`processing_started_at`) expired because the worker
  that held it died. No row means another delivery owns it or it is already final.
- Every code path that runs ends the document in `ready`, `failed` or back in `pending`
  (transient provider error, so the task can retry). Never in `processing`: only a killed
  process can leave it there, and the lease covers that.
- Every write after the acquire is *fenced* on `attempts`: if another delivery took the
  document over in the meantime, this one writes nothing.
"""

import logging
import math
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, func, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core import tracing
from app.core.config import Settings
from app.core.errors import (
    EmptyPDFError,
    IngestionError,
    PermanentProviderError,
    TransientProviderError,
)
from app.db.models import Chunk, Document, DocumentStatus, FailureCode
from app.db.session import tenant_transaction
from app.generation.guardrails import detect_injection
from app.ingestion.chunking import chunk_document
from app.ingestion.types import ChunkDraft, TextExtractor
from app.ports import Storage
from app.providers.embeddings.base import EmbeddingsProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionDeps:
    session_factory: sessionmaker[Session]
    storage: Storage
    extractor: TextExtractor
    embeddings: EmbeddingsProvider
    settings: Settings
    # Monotonic seconds, for `processing_ms`. Injected so tests can control it.
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class ProcessOutcome:
    status: Literal["ready", "failed", "skipped"]
    failure_code: FailureCode | None = None
    chunk_count: int | None = None


@dataclass(frozen=True)
class _Claim:
    """What the acquire returns: the delivery number and what is needed to process."""

    attempt: int
    storage_key: str | None
    filename: str


@dataclass(frozen=True)
class _Embedded:
    vectors: list[list[float]]
    calls: int
    tokens: int


class _LeaseLost(Exception):
    """Another delivery took the document over: this one must not write anything."""


# One statement, so two deliveries can never both acquire the same document: Postgres
# locks the row and the second one re-evaluates the WHERE after the first commits.
_ACQUIRE = text(
    """
    UPDATE documents
    SET status = 'processing',
        processing_started_at = now(),
        attempts = attempts + 1,
        updated_at = now()
    WHERE id = :id
      AND (
        status = 'pending'
        OR (status = 'processing'
            AND processing_started_at < now() - make_interval(secs => :lease_s))
      )
    RETURNING attempts, storage_key, filename
    """
)


def process_document(
    document_id: uuid.UUID, tenant_id: uuid.UUID, deps: IngestionDeps
) -> ProcessOutcome:
    """Process one document. Returns the outcome; raises `TransientProviderError` after
    putting the document back to `pending`, so the caller can retry it."""
    claim = _acquire(document_id, tenant_id, deps)
    if claim is None:
        logger.info(
            "document.skipped",
            extra={"document_id": str(document_id), "tenant_id": str(tenant_id)},
        )
        return ProcessOutcome("skipped")

    log_extra = {
        "document_id": str(document_id),
        "tenant_id": str(tenant_id),
        "attempt": claim.attempt,
    }
    tracing.set_attributes(
        **{
            tracing.TENANT_ID: str(tenant_id),
            tracing.DOCUMENT_ID: str(document_id),
            tracing.TASK_ATTEMPT: claim.attempt,
        }
    )
    logger.info("document.processing", extra=log_extra)

    if claim.attempt > deps.settings.task_max_attempts:
        # Delivered too many times without finishing: probably a PDF that kills the
        # worker. Stop here instead of crashing it again.
        detail = f"gave up after {claim.attempt - 1} deliveries that did not finish"
        return _fail(document_id, tenant_id, claim, deps, FailureCode.WORKER_LOST, detail)

    # `settled` is set once the document has left `processing` through one of the paths
    # below. The `finally` covers what `except Exception` does not catch
    # (KeyboardInterrupt, SystemExit): the lease is released instead of left to expire.
    settled = False
    try:
        outcome = _ingest(document_id, tenant_id, claim, deps)
        settled = True
        return outcome
    except _LeaseLost:
        settled = True
        logger.warning("document.lease_lost", extra=log_extra)
        return ProcessOutcome("skipped")
    except TransientProviderError:
        _release(document_id, tenant_id, claim, deps)
        settled = True
        raise
    except IngestionError as exc:
        outcome = _fail(document_id, tenant_id, claim, deps, exc.failure_code, exc.detail)
        settled = True
        return outcome
    except PermanentProviderError as exc:
        detail = f"embedding provider rejected the request: {exc}"
        outcome = _fail(
            document_id, tenant_id, claim, deps, FailureCode.EMBEDDING_UNAVAILABLE, detail
        )
        settled = True
        return outcome
    except SoftTimeLimitExceeded:
        detail = f"processing exceeded {deps.settings.task_soft_time_limit_s} s"
        outcome = _fail(document_id, tenant_id, claim, deps, FailureCode.TIMEOUT, detail)
        settled = True
        return outcome
    except Exception as exc:
        # Unexpected: record only the exception type, never its message, which could
        # contain document content.
        logger.error(
            "document.internal_error", extra={**log_extra, "error_type": type(exc).__name__}
        )
        outcome = _fail(
            document_id, tenant_id, claim, deps, FailureCode.INTERNAL_ERROR, type(exc).__name__
        )
        settled = True
        return outcome
    finally:
        if not settled:
            _release(document_id, tenant_id, claim, deps)


def mark_failed(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    deps: IngestionDeps,
    code: FailureCode,
    detail: str,
) -> bool:
    """Mark a `pending` document as failed. Used by the task when the retries of a
    transient error run out (the last delivery already put it back to `pending`).
    Returns False if the document was not pending (e.g. another delivery has it)."""
    with tenant_transaction(deps.session_factory, tenant_id) as session:
        result = session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == DocumentStatus.PENDING)
            .values(**_failed_values(code, detail))
        )
        changed = result.rowcount == 1
    if changed:
        _log_failed(document_id, tenant_id, code)
    return changed


def title_from_filename(filename: str) -> str:
    """'tire_supply_agreement_frontera.pdf' -> 'tire supply agreement frontera'."""
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename


# --- Steps --------------------------------------------------------------------------------


def _acquire(document_id: uuid.UUID, tenant_id: uuid.UUID, deps: IngestionDeps) -> _Claim | None:
    with tenant_transaction(deps.session_factory, tenant_id) as session:
        row = session.execute(
            _ACQUIRE, {"id": document_id, "lease_s": deps.settings.task_time_limit_s}
        ).one_or_none()
    if row is None:
        return None
    return _Claim(attempt=row.attempts, storage_key=row.storage_key, filename=row.filename)


def _ingest(
    document_id: uuid.UUID, tenant_id: uuid.UUID, claim: _Claim, deps: IngestionDeps
) -> ProcessOutcome:
    settings = deps.settings
    started = deps.clock()
    attributes: dict[str, Any] = {
        tracing.TENANT_ID: str(tenant_id),
        tracing.DOCUMENT_ID: str(document_id),
        tracing.TASK_ATTEMPT: claim.attempt,
    }

    if claim.storage_key is None:
        raise IngestionError("the document has no stored file")
    pdf = deps.storage.read(claim.storage_key)

    with tracing.span("ingest.extract", **attributes) as current:
        extracted = deps.extractor.extract(pdf)
        attributes[tracing.DOCUMENT_PAGES] = extracted.page_count
        tracing.set_attributes(current, **{tracing.DOCUMENT_PAGES: extracted.page_count})

    with tracing.span("ingest.chunk", **attributes) as current:
        drafts = chunk_document(
            extracted,
            title=title_from_filename(claim.filename),
            max_chars=settings.chunk_max_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
        tracing.set_attributes(current, **{tracing.CHUNKS_COUNT: len(drafts)})
    if not drafts:
        # The extractor should have caught this; checked again so a document is never
        # `ready` with zero chunks.
        raise EmptyPDFError("no extractable text")
    attributes[tracing.CHUNKS_COUNT] = len(drafts)

    # Chunks that look like instructions to the assistant (ADR-013): stored like any
    # other chunk, but never given to the LLM. Only the count is recorded, never the text.
    flagged = {draft.chunk_index for draft in drafts if detect_injection(draft.content)}
    tracing.set_attributes(**{tracing.PROMPT_INJECTION_FLAGGED: len(flagged)})
    if flagged:
        logger.warning(
            "document.injection_flagged",
            extra={
                "document_id": str(document_id),
                "tenant_id": str(tenant_id),
                "chunks": len(flagged),
            },
        )

    with tracing.span(
        "ingest.embed",
        **attributes,
        **{
            tracing.EMBEDDING_MODEL: deps.embeddings.model_id,
            tracing.EMBEDDING_BATCH_SIZE: settings.embedding_batch_size,
        },
    ):
        embedded = _embed(
            deps.embeddings, [d.embed_text for d in drafts], settings.embedding_batch_size
        )

    processing_ms = math.ceil((deps.clock() - started) * 1000)
    with tracing.span("ingest.store", **attributes):
        _store(
            document_id,
            tenant_id,
            claim,
            deps,
            drafts=drafts,
            flagged=flagged,
            embedded=embedded,
            page_count=extracted.page_count,
            processing_ms=processing_ms,
        )

    logger.info(
        "document.ready",
        extra={
            "document_id": str(document_id),
            "tenant_id": str(tenant_id),
            "attempt": claim.attempt,
            "pages": extracted.page_count,
            "chunks": len(drafts),
            "processing_ms": processing_ms,
            "embedding_calls": embedded.calls,
            "embedding_tokens": embedded.tokens,
        },
    )
    return ProcessOutcome("ready", chunk_count=len(drafts))


def _embed(provider: EmbeddingsProvider, texts: Sequence[str], batch_size: int) -> _Embedded:
    vectors: list[list[float]] = []
    calls = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batch_vectors = provider.embed_documents(batch)
        calls += 1
        if len(batch_vectors) != len(batch):
            raise PermanentProviderError(f"expected {len(batch)} vectors, got {len(batch_vectors)}")
        vectors.extend(batch_vectors)
    return _Embedded(vectors=vectors, calls=calls, tokens=provider.count_tokens(texts))


def _store(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim: _Claim,
    deps: IngestionDeps,
    *,
    drafts: list[ChunkDraft],
    flagged: set[int],
    embedded: _Embedded,
    page_count: int,
    processing_ms: int,
) -> None:
    """Replace the document's chunks and mark it `ready`, all in one transaction:
    readers see either the old state or the new one, never a half-written document."""
    with tenant_transaction(deps.session_factory, tenant_id) as session:
        # Fenced first: if the lease was lost, raise and roll back before touching chunks.
        result = session.execute(
            update(Document)
            .where(*_owned_by(document_id, claim))
            .values(
                status=DocumentStatus.READY,
                page_count=page_count,
                chunk_count=len(drafts),
                embedding_model=deps.embeddings.model_id,
                ingest_trace_id=tracing.current_trace_id(),
                ingest_span_id=tracing.current_span_id(),
                processing_ms=processing_ms,
                embedding_calls=embedded.calls,
                embedding_tokens=embedded.tokens,
                embedding_tokens_estimated=deps.embeddings.tokens_estimated,
                processing_started_at=None,
                failure_code=None,
                failure_detail=None,
                # A finished run resets the delivery counter: a later reindex starts at 0
                # instead of inheriting this run's deliveries.
                attempts=0,
                updated_at=func.now(),
            )
        )
        if result.rowcount != 1:
            raise _LeaseLost()

        # Idempotent: a re-run replaces the chunks instead of adding to them.
        session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        for draft, vector in zip(drafts, embedded.vectors, strict=True):
            chunk = Chunk(
                document_id=document_id,
                tenant_id=tenant_id,
                page=draft.page,
                chunk_index=draft.chunk_index,
                content=draft.content,
                embedding=vector,
                page_width=draft.page_width,
                page_height=draft.page_height,
                flagged_injection=draft.chunk_index in flagged,
            )
            # Only set when known: assigning None to a JSONB attribute stores the JSON
            # value `null`, not SQL NULL, and the bbox CHECK rejects it.
            if draft.bbox is not None:
                chunk.bbox = draft.bbox.as_list()
            session.add(chunk)


def _fail(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    claim: _Claim,
    deps: IngestionDeps,
    code: FailureCode,
    detail: str,
) -> ProcessOutcome:
    tracing.set_attributes(**{tracing.FAILURE_CODE: code.value})
    with tenant_transaction(deps.session_factory, tenant_id) as session:
        result = session.execute(
            update(Document)
            .where(*_owned_by(document_id, claim))
            .values(**_failed_values(code, detail))
        )
        if result.rowcount != 1:
            return ProcessOutcome("skipped")
    _log_failed(document_id, tenant_id, code, claim.attempt)
    return ProcessOutcome("failed", failure_code=code)


def _release(
    document_id: uuid.UUID, tenant_id: uuid.UUID, claim: _Claim, deps: IngestionDeps
) -> None:
    """Back to `pending` so a retry can acquire it again. The delivery is given back
    (`attempts - 1`): it ended cleanly, so it must not count towards `worker_lost`."""
    with tenant_transaction(deps.session_factory, tenant_id) as session:
        session.execute(
            update(Document)
            .where(*_owned_by(document_id, claim))
            .values(
                status=DocumentStatus.PENDING,
                processing_started_at=None,
                attempts=Document.attempts - 1,
                updated_at=func.now(),
            )
        )


def _owned_by(document_id: uuid.UUID, claim: _Claim) -> tuple[Any, ...]:
    """WHERE clause of every write after the acquire: still processing, and still this
    delivery (a takeover would have incremented `attempts`)."""
    return (
        Document.id == document_id,
        Document.status == DocumentStatus.PROCESSING,
        Document.attempts == claim.attempt,
    )


def _failed_values(code: FailureCode, detail: str) -> dict[str, Any]:
    # Both CHECKs hold: `failed` has a code, and leaving `processing` clears the lease.
    return {
        "status": DocumentStatus.FAILED,
        "failure_code": code.value,
        "failure_detail": detail,
        "processing_started_at": None,
        "updated_at": func.now(),
    }


def _log_failed(
    document_id: uuid.UUID, tenant_id: uuid.UUID, code: FailureCode, attempt: int | None = None
) -> None:
    logger.warning(
        "document.failed",
        extra={
            "document_id": str(document_id),
            "tenant_id": str(tenant_id),
            "failure_code": code.value,
            "attempt": attempt,
        },
    )

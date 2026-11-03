"""The ingestion pipeline against the real Postgres: state transitions, idempotent
writes, failure codes and the lease. The extractor is scripted and the storage is in
memory, so no Docling and no disk are needed."""

import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import (
    CorruptPDFError,
    EmptyPDFError,
    PermanentProviderError,
    TransientProviderError,
)
from app.db.models import Document, FailureCode
from app.db.session import tenant_transaction
from app.ingestion.pipeline import mark_failed, process_document
from app.ingestion.types import ExtractedDocument
from tests.ingestion_doubles import (
    SAMPLE_CHUNKS,
    FlakyEmbeddings,
    InMemoryStorage,
    ScriptedExtractor,
    insert_document,
    load_chunks,
    load_document,
    make_deps,
)


@pytest.fixture
def tenant(two_tenants) -> uuid.UUID:
    return two_tenants[0]


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


def _set_columns(
    sessions: sessionmaker[Session], tenant_id: uuid.UUID, document_id: uuid.UUID, **values
) -> None:
    with tenant_transaction(sessions, tenant_id) as session:
        session.execute(update(Document).where(Document.id == document_id).values(**values))


def _processing_since(age: timedelta) -> dict:
    """Columns of a document some other delivery acquired `age` ago."""
    return {"status": "processing", "processing_started_at": func.now() - age}


# --- Happy path ---------------------------------------------------------------------------


def test_ready_path_stores_chunks_and_metrics(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    ticks = iter([100.0, 100.25])
    deps = replace(make_deps(app_sessions, storage), clock=lambda: next(ticks))

    outcome = process_document(document_id, tenant, deps)

    assert (outcome.status, outcome.chunk_count) == ("ready", SAMPLE_CHUNKS)
    document = load_document(app_sessions, tenant, document_id)
    assert document.status == "ready"
    assert (document.page_count, document.chunk_count) == (2, SAMPLE_CHUNKS)
    assert document.embedding_model == "fake-hash-v1@768"
    assert document.processing_started_at is None
    assert (document.failure_code, document.failure_detail) == (None, None)
    assert document.processing_ms == 250
    # Three chunks in batches of two: two calls.
    assert document.embedding_calls == 2
    assert document.embedding_tokens > 0
    assert document.embedding_tokens_estimated is True

    chunks = load_chunks(app_sessions, tenant, document_id)
    assert [(c.page, c.chunk_index) for c in chunks] == [(1, 0), (1, 1), (2, 2)]
    assert chunks[0].content.startswith("1. Delivery\nSupplier shall deliver")
    assert chunks[0].bbox == [72, 72, 540, 130]
    assert chunks[2].bbox is None
    assert (chunks[0].page_width, chunks[0].page_height) == (612.0, 792.0)
    assert all(c.tenant_id == tenant for c in chunks)
    # The stored vector is the one of `embed_text` (with the context prefix).
    assert len(chunks[0].embedding) == 768


def test_rerun_replaces_chunks_without_duplicates(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    deps = make_deps(app_sessions, storage)
    process_document(document_id, tenant, deps)
    first_ids = {c.id for c in load_chunks(app_sessions, tenant, document_id)}

    # A reindex puts the document back to pending.
    _set_columns(app_sessions, tenant, document_id, status="pending")
    outcome = process_document(document_id, tenant, deps)

    assert outcome.status == "ready"
    chunks = load_chunks(app_sessions, tenant, document_id)
    assert len(chunks) == SAMPLE_CHUNKS
    assert load_document(app_sessions, tenant, document_id).chunk_count == SAMPLE_CHUNKS
    # The old rows were replaced, not kept next to the new ones.
    assert first_ids.isdisjoint(c.id for c in chunks)


# --- Permanent failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CorruptPDFError("could not parse the PDF"), FailureCode.CORRUPT_PDF),
        (EmptyPDFError("no extractable text"), FailureCode.EMPTY_PDF),
        (SoftTimeLimitExceeded(), FailureCode.TIMEOUT),
    ],
)
def test_extraction_failures_end_failed_with_their_code(app_sessions, tenant, storage, error, code):
    document_id = insert_document(app_sessions, storage, tenant)
    deps = make_deps(app_sessions, storage, extractor=ScriptedExtractor(error=error))

    # A permanent failure is returned, not raised: the task does not retry it.
    outcome = process_document(document_id, tenant, deps)

    assert (outcome.status, outcome.failure_code) == ("failed", code)
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.failure_code) == ("failed", code.value)
    assert document.failure_detail
    assert document.processing_started_at is None
    assert load_chunks(app_sessions, tenant, document_id) == []


def test_document_without_text_after_chunking_is_empty(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    blank = ScriptedExtractor(document=ExtractedDocument(pages=[]))

    outcome = process_document(
        document_id, tenant, make_deps(app_sessions, storage, extractor=blank)
    )

    assert outcome.failure_code == FailureCode.EMPTY_PDF


def test_unexpected_error_is_internal_and_hides_its_message(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    error = RuntimeError("Supplier shall pay USD 0.02 per gallon")  # document content
    deps = make_deps(app_sessions, storage, extractor=ScriptedExtractor(error=error))

    outcome = process_document(document_id, tenant, deps)

    assert outcome.failure_code == FailureCode.INTERNAL_ERROR
    document = load_document(app_sessions, tenant, document_id)
    assert document.failure_detail == "RuntimeError"


def test_permanent_provider_error_is_embedding_unavailable(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    embeddings = FlakyEmbeddings(PermanentProviderError("400 bad request"), failures=None)

    outcome = process_document(
        document_id, tenant, make_deps(app_sessions, storage, embeddings=embeddings)
    )

    assert outcome.failure_code == FailureCode.EMBEDDING_UNAVAILABLE
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.failure_code) == ("failed", "embedding_unavailable")


# --- Transient failures -------------------------------------------------------------------


def test_transient_error_puts_document_back_to_pending(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    embeddings = FlakyEmbeddings(TransientProviderError("503"), failures=1)
    deps = make_deps(app_sessions, storage, embeddings=embeddings)

    with pytest.raises(TransientProviderError):
        process_document(document_id, tenant, deps)

    document = load_document(app_sessions, tenant, document_id)
    assert document.status == "pending"
    assert document.processing_started_at is None
    # A clean release gives the delivery back: it must not count towards worker_lost.
    assert document.attempts == 0
    assert load_chunks(app_sessions, tenant, document_id) == []

    # The retry finds it pending and finishes it.
    assert process_document(document_id, tenant, deps).status == "ready"
    assert len(load_chunks(app_sessions, tenant, document_id)) == SAMPLE_CHUNKS


def test_mark_failed_after_retries_only_touches_pending_documents(app_sessions, tenant, storage):
    pending = insert_document(app_sessions, storage, tenant)
    ready = insert_document(app_sessions, storage, tenant, filename="other.pdf")
    deps = make_deps(app_sessions, storage)
    process_document(ready, tenant, deps)

    code = FailureCode.EMBEDDING_UNAVAILABLE
    assert mark_failed(pending, tenant, deps, code, "provider down") is True
    assert mark_failed(ready, tenant, deps, code, "provider down") is False

    assert load_document(app_sessions, tenant, pending).failure_code == "embedding_unavailable"
    assert load_document(app_sessions, tenant, ready).status == "ready"


# --- Lease and deliveries -----------------------------------------------------------------


def test_expired_lease_is_taken_over(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    # A worker acquired it long ago and died: no `finally` ran.
    _set_columns(
        app_sessions, tenant, document_id, attempts=1, **_processing_since(timedelta(hours=1))
    )

    outcome = process_document(document_id, tenant, make_deps(app_sessions, storage))

    assert outcome.status == "ready"


def test_fresh_lease_is_skipped(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    # Another delivery is working on it right now.
    _set_columns(app_sessions, tenant, document_id, attempts=1, **_processing_since(timedelta(0)))
    extractor = ScriptedExtractor()

    outcome = process_document(
        document_id, tenant, make_deps(app_sessions, storage, extractor=extractor)
    )

    assert outcome.status == "skipped"
    assert extractor.calls == 0
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.attempts) == ("processing", 1)


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_final_documents_are_skipped(app_sessions, tenant, storage, status):
    document_id = insert_document(app_sessions, storage, tenant)
    failure = {"failure_code": "corrupt_pdf"} if status == "failed" else {}
    _set_columns(app_sessions, tenant, document_id, status=status, **failure)

    assert (
        process_document(document_id, tenant, make_deps(app_sessions, storage)).status == "skipped"
    )


def test_too_many_deliveries_is_worker_lost(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)
    # Three deliveries (the maximum) already took it and none finished.
    _set_columns(
        app_sessions, tenant, document_id, attempts=3, **_processing_since(timedelta(hours=1))
    )
    extractor = ScriptedExtractor()

    outcome = process_document(
        document_id, tenant, make_deps(app_sessions, storage, extractor=extractor)
    )

    assert outcome.failure_code == FailureCode.WORKER_LOST
    assert extractor.calls == 0
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.failure_code) == ("failed", "worker_lost")
    assert document.processing_started_at is None


def test_delivery_that_lost_its_lease_writes_nothing(app_sessions, tenant, storage):
    document_id = insert_document(app_sessions, storage, tenant)

    class TakenOverWhileExtracting(ScriptedExtractor):
        def extract(self, pdf: bytes) -> ExtractedDocument:
            # Simulates a slow delivery whose lease expired and was taken over.
            _set_columns(app_sessions, tenant, document_id, attempts=Document.attempts + 1)
            return super().extract(pdf)

    deps = make_deps(app_sessions, storage, extractor=TakenOverWhileExtracting())
    outcome = process_document(document_id, tenant, deps)

    assert outcome.status == "skipped"
    assert load_chunks(app_sessions, tenant, document_id) == []
    # Still owned by the delivery that took it over.
    assert load_document(app_sessions, tenant, document_id).status == "processing"


def test_other_tenant_cannot_process_the_document(app_sessions, two_tenants, storage):
    tenant_a, tenant_b = two_tenants
    document_id = insert_document(app_sessions, storage, tenant_a)

    # RLS: under tenant B the row does not exist.
    outcome = process_document(document_id, tenant_b, make_deps(app_sessions, storage))

    assert outcome.status == "skipped"
    assert load_document(app_sessions, tenant_a, document_id).status == "pending"

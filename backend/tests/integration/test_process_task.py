"""The Celery task, run eagerly (`task.apply()`, no broker) against the real Postgres:
retries of transient provider errors, and the final `failed` when they run out."""

import logging
import uuid

import pytest

from app.core.errors import TransientProviderError
from app.worker import tasks
from tests.ingestion_doubles import (
    SAMPLE_CHUNKS,
    FlakyEmbeddings,
    InMemoryStorage,
    insert_document,
    load_chunks,
    load_document,
    make_deps,
)


@pytest.fixture
def run_task(app_sessions, two_tenants, monkeypatch, caplog):
    """Insert a document and run the task on it with the given embeddings."""
    tenant = two_tenants[0]
    storage = InMemoryStorage()
    caplog.set_level(logging.INFO, logger="app.worker.tasks")

    def _run(embeddings: FlakyEmbeddings) -> tuple[uuid.UUID, uuid.UUID, str]:
        document_id = insert_document(app_sessions, storage, tenant)
        deps = make_deps(app_sessions, storage, embeddings=embeddings)
        monkeypatch.setattr(tasks, "build_deps", lambda: deps)
        result = tasks.process_document_task.apply(args=[str(document_id), str(tenant)])
        return tenant, document_id, result.get()

    return _run


def _retries_scheduled(caplog) -> int:
    return sum(1 for r in caplog.records if r.getMessage() == "document.retry_scheduled")


def test_task_is_registered_with_the_shared_name():
    assert tasks.process_document_task.name == "rutaviva.process_document"


def test_transient_error_once_is_retried_and_ends_ready(run_task, app_sessions, caplog):
    embeddings = FlakyEmbeddings(TransientProviderError("503"), failures=1)

    tenant, document_id, status = run_task(embeddings)

    assert status == "ready"
    assert _retries_scheduled(caplog) == 1
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.chunk_count) == ("ready", SAMPLE_CHUNKS)
    assert len(load_chunks(app_sessions, tenant, document_id)) == SAMPLE_CHUNKS


def test_transient_error_always_ends_failed_after_max_retries(run_task, app_sessions, caplog):
    embeddings = FlakyEmbeddings(TransientProviderError("503"), failures=None)

    tenant, document_id, status = run_task(embeddings)

    # task_max_retries=2 in the test settings: the first run plus two retries.
    assert status == "failed"
    assert _retries_scheduled(caplog) == 2
    assert embeddings.calls == 3
    document = load_document(app_sessions, tenant, document_id)
    assert (document.status, document.failure_code) == ("failed", "embedding_unavailable")
    assert document.processing_started_at is None
    assert document.failure_detail.startswith("embedding provider unavailable after 3 attempts")

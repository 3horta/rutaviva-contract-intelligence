"""Ports between modules: small interfaces so the API, the worker and the tests can swap
implementations without importing each other.

Ports and adapters are used only where there is a real substitution (storage on disk vs
in memory in tests, Celery vs an in-process dispatcher in tests). The rest of the code
talks to SQLAlchemy and Postgres directly on purpose.
"""

import uuid
from typing import Protocol


class Storage(Protocol):
    """Where uploaded PDFs live. The only implementation in the app is local disk."""

    def save(self, tenant_id: uuid.UUID, document_id: uuid.UUID, data: bytes) -> str:
        """Store the file and return its storage key (unique per upload)."""
        ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


class TaskDispatcher(Protocol):
    """Enqueues background work. The API only calls this *after* the transaction that
    created the document has committed, so the worker never looks for a missing row."""

    def enqueue_document(self, document_id: uuid.UUID, tenant_id: uuid.UUID) -> None: ...

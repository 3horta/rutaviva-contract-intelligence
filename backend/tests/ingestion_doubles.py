"""Test doubles and helpers for the ingestion tests (pipeline and Celery task)."""

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Chunk, Document
from app.db.session import tenant_transaction
from app.ingestion.pipeline import IngestionDeps
from app.ingestion.types import BBox, ExtractedDocument, PageText, TextBlock
from app.providers.embeddings.fake import FakeEmbeddings


class InMemoryStorage:
    """Storage port backed by a dict."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def save(self, tenant_id: uuid.UUID, document_id: uuid.UUID, data: bytes) -> str:
        key = f"{tenant_id}/{document_id}/{uuid.uuid4()}.pdf"
        self.files[key] = data
        return key

    def read(self, key: str) -> bytes:
        return self.files[key]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)


class ScriptedExtractor:
    """Returns a hand-built document, or raises the given error."""

    def __init__(
        self, document: ExtractedDocument | None = None, error: Exception | None = None
    ) -> None:
        self.document = document or sample_document()
        self.error = error
        self.calls = 0

    def extract(self, pdf: bytes) -> ExtractedDocument:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.document


class FlakyEmbeddings(FakeEmbeddings):
    """Fake embeddings that raise `error` on the first `failures` calls (forever if
    `failures` is None), then behave normally."""

    def __init__(self, error: Exception, failures: int | None = 1, dim: int = 768) -> None:
        super().__init__(dim=dim)
        self.error = error
        self.failures = failures
        self.calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.failures is None or self.calls <= self.failures:
            raise self.error
        return super().embed_documents(texts)


def sample_document() -> ExtractedDocument:
    """Two pages, three chunks with the settings of `ingestion_settings` (max 200 chars)."""
    return ExtractedDocument(
        pages=[
            PageText(
                number=1,
                width=612.0,
                height=792.0,
                blocks=[
                    TextBlock("1. Delivery", BBox(72, 72, 200, 90), kind="heading"),
                    TextBlock(
                        "Supplier shall deliver the tires within 72 business hours of "
                        "each purchase order issued by Rutaviva.",
                        BBox(72, 100, 540, 130),
                    ),
                    TextBlock(
                        "A late delivery incurs a penalty of 1.5% per business day of "
                        "delay, capped at 15% of the order value.",
                        BBox(72, 140, 540, 170),
                    ),
                ],
            ),
            PageText(
                number=2,
                width=612.0,
                height=792.0,
                blocks=[TextBlock("2. Term. This agreement lasts two years.", None)],
            ),
        ]
    )


SAMPLE_CHUNKS = 3


def ingestion_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "chunk_max_chars": 200,
        "chunk_overlap_chars": 0,
        "embedding_batch_size": 2,
        "task_max_retries": 2,
        "task_max_attempts": 3,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_deps(
    sessions: sessionmaker[Session],
    storage: InMemoryStorage,
    *,
    extractor: ScriptedExtractor | None = None,
    embeddings: FakeEmbeddings | None = None,
    settings: Settings | None = None,
) -> IngestionDeps:
    return IngestionDeps(
        session_factory=sessions,
        storage=storage,
        extractor=extractor or ScriptedExtractor(),
        embeddings=embeddings or FakeEmbeddings(dim=768),
        settings=settings or ingestion_settings(),
    )


def insert_document(
    sessions: sessionmaker[Session], storage: InMemoryStorage, tenant_id: uuid.UUID, **values: Any
) -> uuid.UUID:
    """A `pending` document whose PDF is in `storage`. `values` override columns."""
    document_id = uuid.uuid4()
    data = f"%PDF-1.7 fake {document_id}".encode()
    columns: dict[str, Any] = {
        "id": document_id,
        "tenant_id": tenant_id,
        "filename": "tire_supply_agreement_frontera.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "storage_key": storage.save(tenant_id, document_id, data),
    }
    columns.update(values)
    with tenant_transaction(sessions, tenant_id) as session:
        session.add(Document(**columns))
    return document_id


def load_document(
    sessions: sessionmaker[Session], tenant_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    with tenant_transaction(sessions, tenant_id) as session:
        return session.get_one(Document, document_id)


def load_chunks(
    sessions: sessionmaker[Session], tenant_id: uuid.UUID, document_id: uuid.UUID
) -> list[Chunk]:
    with tenant_transaction(sessions, tenant_id) as session:
        return list(
            session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
            )
        )

"""Database models.

Single source of the schema: the migration is generated from here with
`alembic revision --autogenerate` and then edited by hand (extension, RLS and roles).

Tenant isolation:
- Every table holding tenant data has `tenant_id`, child tables included
  (`chunks`, `messages`), so queries filter without a JOIN and RLS stays trivial.
- Child tables reference their parent with a composite FK `(parent_id, tenant_id)`: the
  database prevents a chunk or a message from having a different tenant than its parent.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import SCHEMA_EMBEDDING_DIM
from app.db.base import Base

# Full-text search dictionary: the contracts are in English.
FTS_CONFIG = "english"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class FailureCode(StrEnum):
    """Closed set of reasons a document ends in `failed`. The UI and the tests
    branch on the code; `failure_detail` carries the human-readable explanation."""

    CORRUPT_PDF = "corrupt_pdf"  # cannot be opened or parsed
    EMPTY_PDF = "empty_pdf"  # no extractable text (e.g. a scan; OCR is out of scope)
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"  # provider failed after retries
    TIMEOUT = "timeout"  # the task exceeded its time limit
    WORKER_LOST = "worker_lost"  # too many deliveries without finishing
    INTERNAL_ERROR = "internal_error"  # anything unexpected; never left in `processing`


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    # The client disconnected (Stop) before the answer was saved: the question is kept,
    # with an empty answer. Never used as context for follow-ups.
    INTERRUPTED = "interrupted"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FeedbackRating(StrEnum):
    UP = "up"
    DOWN = "down"


# Longest comment kept with a rating (after trimming).
FEEDBACK_COMMENT_MAX_CHARS = 1000


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


def _created_at() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    # `onupdate` only applies to UPDATEs issued through the ORM; a raw SQL UPDATE
    # (e.g. the worker's atomic transition) must set `updated_at = now()` itself.
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class Tenant(Base):
    """Tenant. No RLS: authentication checks that the tenant in a verified token exists
    before any tenant is set. The app role can only read it."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Stable slug used in tokens and sample data (e.g. "tenant_a").
    name: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = _created_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # The same PDF cannot be live twice in a tenant; a `failed` one does not count,
        # so it can be uploaded again as a new document. Partial unique index: the
        # database closes the race between two simultaneous uploads.
        Index(
            "uq_documents_tenant_id_sha256_live",
            "tenant_id",
            "sha256",
            unique=True,
            postgresql_where=text("status <> 'failed'"),
        ),
        # Target of the composite FKs from `chunks`.
        UniqueConstraint("id", "tenant_id"),
        CheckConstraint(_in("status", DocumentStatus), name="status"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint(
            f"failure_code IS NULL OR {_in('failure_code', FailureCode)}", name="failure_code"
        ),
        # A failed document always says why, and only failed documents carry a code.
        CheckConstraint("(status = 'failed') = (failure_code IS NOT NULL)", name="failure_status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The lease only exists while processing: leaving `processing` must clear it,
        # and entering `processing` requires setting it.
        CheckConstraint(
            "(status = 'processing') = (processing_started_at IS NOT NULL)",
            name="processing_lease",
        ),
        # Tenant listing, newest first.
        Index(None, "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        Text, default=DocumentStatus.PENDING, server_default=DocumentStatus.PENDING
    )
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None]
    chunk_count: Mapped[int | None]
    # Embedding version the document was indexed with.
    embedding_model: Mapped[str | None] = mapped_column(Text)
    # Span links from a query trace to the ingestion trace of each retrieved document
    # (a link needs both the trace id and a span id of that trace).
    ingest_trace_id: Mapped[str | None] = mapped_column(Text)
    ingest_span_id: Mapped[str | None] = mapped_column(Text)
    # Lease against stuck documents. If the worker dies midway, no `finally` runs;
    # a Celery redelivery may take over a `processing` document whose lease expired
    # (older than the task's time_limit).
    processing_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Number of task deliveries that picked up the document. Past the maximum it is marked
    # `failed` without processing: a PDF that crashes the worker is not redelivered forever.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    # Key of the stored PDF in the Storage port (unique per upload).
    storage_key: Mapped[str | None] = mapped_column(Text)
    # Per-document metrics of the last successful ingestion (cost and latency).
    processing_ms: Mapped[int | None]
    embedding_calls: Mapped[int | None]
    embedding_tokens: Mapped[int | None]
    embedding_tokens_estimated: Mapped[bool | None]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "tenant_id"],
            ["documents.id", "documents.tenant_id"],
            ondelete="CASCADE",
        ),
        # Basis for the worker's idempotent writes.
        UniqueConstraint("document_id", "chunk_index"),
        CheckConstraint("page >= 1", name="page_positive"),
        CheckConstraint(
            "bbox IS NULL OR (jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4)",
            name="bbox_four_numbers",
        ),
        Index(None, "tenant_id"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv_gin", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]
    page: Mapped[int]
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(SCHEMA_EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{FTS_CONFIG}', content)", persisted=True)
    )
    # Set by the injection detector at indexing time.
    flagged_injection: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Where the chunk is on its page, for highlighting: [x0, top, x1, bottom] in PDF
    # points, origin top-left. Null if the extractor gave no coordinates.
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)
    page_width: Mapped[float | None]
    page_height: Mapped[float | None]

    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(Base):
    """A tenant's question thread. It stores no context of its own: the context is
    read from its messages."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        # Listing by most recent activity.
        Index(None, "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    # The first question, shown in the listing.
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.sequence",
    )


class Message(Base):
    """A message as displayed. The question and its answer are written together in
    one transaction: if the query fails (503/504), neither is stored. If the client
    disconnects first, the question is stored with an empty `interrupted` answer.

    Query metadata lives on the assistant message, with no separate log table; the
    detailed record is in the trace (`trace_id`)."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            ondelete="CASCADE",
        ),
        # Order within the conversation. A timestamp is not enough: the question and
        # the answer are written at the same instant.
        UniqueConstraint("conversation_id", "sequence"),
        CheckConstraint(_in("role", MessageRole), name="role"),
        CheckConstraint(f"status IS NULL OR {_in('status', AnswerStatus)}", name="status"),
        CheckConstraint(
            f"confidence IS NULL OR {_in('confidence', Confidence)}", name="confidence"
        ),
        # Only answers have a status.
        CheckConstraint("(role = 'assistant') = (status IS NOT NULL)", name="status_role"),
        Index(None, "tenant_id"),
        # Target of the composite FK of `message_feedback`.
        UniqueConstraint("id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]
    sequence: Mapped[int]
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)

    # --- assistant messages only ---
    # The question rewritten as standalone and used for retrieval; null if the
    # original was used (first turn).
    standalone_question: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text)
    # Copy of each citation ({chunk_id, document_id, document_filename, page, quote}):
    # stays readable even if the document is reindexed and its chunks replaced.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    latency_ms: Mapped[int | None]
    llm_model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    trace_id: Mapped[str | None] = mapped_column(Text)
    # How the answer was produced (`AnswerDetails`: retrieved chunk ids, scores, hit
    # counts, attempts, insufficient reason). Ids and numbers only, never content.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class MessageFeedback(Base):
    """A rating of an answer: one per assistant message and rater, replaced when that
    rater rates it again, and deleted with the message (a retry or a deleted
    conversation takes it along).

    `rater_id` is an anonymous id the browser makes up and sends (`X-Rater-Id`); it
    tells raters apart, it does not authenticate anyone (see ADR-019).

    Its own table rather than columns on `messages`: a message is written once, as
    displayed, while feedback changes later, comes from several raters and has its own
    timestamps. Only assistant messages are rated; the service checks it (a CHECK
    cannot look at another row)."""

    __tablename__ = "message_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "tenant_id"],
            ["messages.id", "messages.tenant_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("rating", FeedbackRating), name="rating"),
        CheckConstraint(
            f"comment IS NULL OR char_length(comment) BETWEEN 1 AND {FEEDBACK_COMMENT_MAX_CHARS}",
            name="comment_length",
        ),
        Index(None, "tenant_id"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    rater_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    rating: Mapped[str] = mapped_column(Text)
    # Trimmed; null when the user left none. Never logged.
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

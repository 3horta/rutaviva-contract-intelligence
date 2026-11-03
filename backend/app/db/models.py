"""Modelos de la base de datos (sección 6 del plan y D-019).

Fuente única del esquema: la migración se genera a partir de aquí con
`alembic revision --autogenerate` y se retoca a mano (extensión, RLS y roles).

Aislamiento por inquilino (sección 10.1):
- Toda tabla con datos de un inquilino lleva `tenant_id`, también las hijas
  (`chunks`, `messages`), para filtrar sin JOIN y para que RLS sea trivial.
- Las hijas apuntan al padre con una FK compuesta `(padre_id, tenant_id)`: la base
  impide que un chunk o un mensaje quede con un inquilino distinto al de su padre.
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
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import SCHEMA_EMBEDDING_DIM
from app.db.base import Base

# Diccionario de texto completo: los contratos están en inglés (D-015).
FTS_CONFIG = "english"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


def _created_at() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    # `onupdate` solo actúa en UPDATE hechos con el ORM; un UPDATE en SQL crudo
    # (p. ej. la transición atómica del worker) debe poner `updated_at = now()`.
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class Tenant(Base):
    """Inquilino. Sin RLS: la autenticación busca por `api_key_hash` antes de saber
    quién es el inquilino. El rol de la app solo puede leerla."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True)
    # sha256 en hex de la API key; la key en claro nunca se guarda.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created_at()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        # Búsqueda de duplicados por inquilino. Sin UNIQUE (D-022): un PDF que terminó
        # en `failed` se puede volver a subir como documento nuevo.
        Index(None, "tenant_id", "sha256"),
        # Destino de las FK compuestas de `chunks`.
        UniqueConstraint("id", "tenant_id"),
        CheckConstraint(_in("status", DocumentStatus), name="status"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # El lease solo existe mientras se procesa (D-024): al salir de `processing`
        # hay que limpiarlo, y no se puede entrar en `processing` sin fijarlo.
        CheckConstraint(
            "(status = 'processing') = (processing_started_at IS NOT NULL)",
            name="processing_lease",
        ),
        # Listado del inquilino, del más reciente al más antiguo.
        Index(None, "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        Text, default=DocumentStatus.PENDING, server_default=DocumentStatus.PENDING
    )
    error_reason: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None]
    chunk_count: Mapped[int | None]
    # Versión del embedding con la que se indexó (sección 8.3).
    embedding_model: Mapped[str | None] = mapped_column(Text)
    # Para los span links de la consulta hacia la ingestión (sección 9).
    ingest_trace_id: Mapped[str | None] = mapped_column(Text)
    # Lease contra documentos colgados (D-024). Si el worker muere a mitad, no corre
    # ningún `finally`; la reentrega de Celery puede retomar un `processing` cuyo
    # lease caducó (más antiguo que el time_limit de la tarea).
    processing_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Veces que una entrega de la tarea tomó el documento. A partir del máximo se marca
    # `failed` sin procesar: un PDF que tumba al worker no se reentrega en bucle.
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
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
        # Base de la escritura idempotente del worker (sección 7.10).
        UniqueConstraint("document_id", "chunk_index"),
        CheckConstraint("page >= 1", name="page_positive"),
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
    # El detector de inyección lo marca al indexar (sección 10.2).
    flagged_injection: Mapped[bool] = mapped_column(default=False, server_default="false")

    document: Mapped[Document] = relationship(back_populates="chunks")


class Conversation(Base):
    """Hilo de preguntas por inquilino (D-019). No guarda contexto propio: el contexto
    se lee de sus mensajes."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id"),
        # Listado por actividad más reciente.
        Index(None, "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    # La primera pregunta, para el listado.
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
    """Un mensaje tal como se muestra. La pregunta y su respuesta se escriben juntas
    en una transacción: si la consulta falla (503/504), no se guarda ninguna.

    Los metadatos de la consulta van en el mensaje del asistente, sin tabla de log
    aparte (D-021); el rastro detallado está en la traza (`trace_id`)."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id"],
            ["conversations.id", "conversations.tenant_id"],
            ondelete="CASCADE",
        ),
        # Orden dentro de la conversación. Un timestamp no basta: la pregunta y la
        # respuesta se escriben en el mismo instante.
        UniqueConstraint("conversation_id", "sequence"),
        CheckConstraint(_in("role", MessageRole), name="role"),
        CheckConstraint(f"status IS NULL OR {_in('status', AnswerStatus)}", name="status"),
        CheckConstraint(
            f"confidence IS NULL OR {_in('confidence', Confidence)}", name="confidence"
        ),
        # Solo las respuestas tienen estado.
        CheckConstraint("(role = 'assistant') = (status IS NOT NULL)", name="status_role"),
        Index(None, "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]
    sequence: Mapped[int]
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)

    # --- solo en mensajes del asistente ---
    # La pregunta reescrita como independiente con la que se buscó; null si se usó
    # la original (primer turno).
    standalone_question: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text)
    # Copia de cada cita ({chunk_id, document_id, page, quote}): sigue legible
    # aunque el documento se reindexe y sus chunks se reemplacen.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    latency_ms: Mapped[int | None]
    llm_model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

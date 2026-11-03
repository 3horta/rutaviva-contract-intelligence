"""API models for documents. The frontend types mirror these (generated from OpenAPI)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DocumentStatusOut = Literal["pending", "processing", "ready", "failed"]
FailureCodeOut = Literal[
    "corrupt_pdf", "empty_pdf", "embedding_unavailable", "timeout", "worker_lost", "internal_error"
]
DocumentOrdering = Literal[
    "created_at", "-created_at", "filename", "-filename", "status", "-status"
]


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatusOut
    # True when the same PDF is already live in this tenant: no new document is created.
    duplicate: bool


class DocumentMetrics(BaseModel):
    processing_ms: int
    embedding_calls: int
    embedding_tokens: int
    tokens_estimated: bool


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: DocumentStatusOut
    failure_code: FailureCodeOut | None
    failure_detail: str | None
    page_count: int | None
    chunk_count: int | None
    embedding_model: str | None
    metrics: DocumentMetrics | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    count: int
    limit: int
    offset: int
    results: list[DocumentOut]


class StatusCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    ready: int = 0
    failed: int = 0


class DocumentStats(BaseModel):
    total: int
    by_status: StatusCounts


class ChunkOut(BaseModel):
    """A stored chunk, as shown when a citation is opened."""

    id: uuid.UUID
    document_id: uuid.UUID
    page: int
    chunk_index: int
    content: str
    # [x0, top, x1, bottom] in PDF points, origin top-left; None if unknown.
    bbox: list[float] | None
    page_width: float | None
    page_height: float | None

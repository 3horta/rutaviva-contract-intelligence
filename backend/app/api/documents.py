"""Documents endpoints: upload, list, stats, detail, file and chunk.

Every query is scoped to the tenant twice: an explicit `tenant_id` filter in the code and
RLS in the database (ADR-002). A document of another tenant answers 404, never 403, so
its existence is not revealed.
"""

import hashlib
import logging
import re
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    CurrentTenant,
    TenantSession,
    get_dispatcher,
    get_session_factory,
    get_storage,
)
from app.core import tracing
from app.core.config import Settings, get_settings
from app.core.errors import (
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    Unprocessable,
    UnsupportedMediaType,
)
from app.db.models import Chunk, Document, DocumentStatus, FailureCode
from app.db.session import tenant_transaction
from app.ports import Storage, TaskDispatcher
from app.schemas.documents import (
    ChunkOut,
    DocumentList,
    DocumentMetrics,
    DocumentOrdering,
    DocumentOut,
    DocumentStats,
    DocumentStatusOut,
    StatusCounts,
    UploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

PDF_MAGIC = b"%PDF-"
READ_CHUNK_BYTES = 1024 * 1024
MAX_FILENAME_CHARS = 200
DEFAULT_FILENAME = "document.pdf"
LIVE_SHA256_INDEX = "uq_documents_tenant_id_sha256_live"
ENQUEUE_FAILED_DETAIL = "could not enqueue processing"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_ORDERING_COLUMNS = {
    "created_at": Document.created_at,
    "filename": Document.filename,
    "status": Document.status,
}


# --- Upload -----------------------------------------------------------------------------


@router.post(
    "",
    status_code=202,
    response_model=UploadResponse,
    responses={200: {"model": UploadResponse, "description": "Already uploaded (duplicate)"}},
)
def upload_document(
    response: Response,
    tenant: CurrentTenant,
    settings: Annotated[Settings, Depends(get_settings)],
    factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    storage: Annotated[Storage, Depends(get_storage)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_dispatcher)],
    file: Annotated[UploadFile, File(description="The contract, as a PDF")],
) -> UploadResponse:
    """Upload a PDF. `202` when a new document is queued for processing; `200` with
    `duplicate: true` when the same file is already live in this tenant."""
    data = _read_limited(file, settings.max_upload_mb * 1024 * 1024)
    if not data:
        raise Unprocessable("The file is empty.")
    # The content decides, not the declared content type or the extension.
    if not data.startswith(PDF_MAGIC):
        raise UnsupportedMediaType("Only PDF files are accepted.")

    sha256 = hashlib.sha256(data).hexdigest()
    filename = sanitize_filename(file.filename)
    with tenant_transaction(factory, tenant.id) as session:
        existing = _find_live_duplicate(session, tenant.id, sha256)
    if existing is not None:
        return _duplicate(response, existing, tenant.id)

    document_id = uuid.uuid4()
    # The file is written before the row, so a committed document always has its file.
    storage_key = storage.save(tenant.id, document_id, data)
    try:
        with tenant_transaction(factory, tenant.id) as session:
            session.add(
                Document(
                    id=document_id,
                    tenant_id=tenant.id,
                    filename=filename,
                    sha256=sha256,
                    status=DocumentStatus.PENDING,
                    storage_key=storage_key,
                )
            )
    except IntegrityError as exc:
        storage.delete(storage_key)
        if _violated_constraint(exc) != LIVE_SHA256_INDEX:
            raise
        # Lost the race: another upload of the same file committed between our check and
        # our insert. The partial unique index stopped the second copy; return the winner.
        with tenant_transaction(factory, tenant.id) as session:
            existing = _find_live_duplicate(session, tenant.id, sha256)
        if existing is None:  # the winner failed in the meantime; very unlikely
            raise
        return _duplicate(response, existing, tenant.id)
    except Exception:
        storage.delete(storage_key)
        raise

    tracing.set_attributes(
        **{tracing.TENANT_ID: str(tenant.id), tracing.DOCUMENT_ID: str(document_id)}
    )

    # Only after the commit: the worker must find the row when the task starts.
    try:
        dispatcher.enqueue_document(document_id, tenant.id)
    except Exception:
        logger.exception(
            "documents.enqueue_failed",
            extra={"tenant_id": str(tenant.id), "document_id": str(document_id)},
        )
        _mark_enqueue_failed(factory, tenant.id, document_id)
        raise ServiceUnavailable(
            "The file was received but could not be queued; try again later."
        ) from None

    logger.info(
        "documents.uploaded",
        extra={
            "tenant_id": str(tenant.id),
            "document_id": str(document_id),
            "size_bytes": len(data),
        },
    )
    return UploadResponse(document_id=document_id, status="pending", duplicate=False)


def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks and stop as soon as it exceeds `max_bytes`.

    Starlette has already spooled the multipart body to a temporary file; this limit keeps
    the file out of memory and the database. Capping the raw request size belongs to the
    reverse proxy (`client_max_body_size`).
    """
    parts: list[bytes] = []
    size = 0
    # A plain `def` endpoint runs in a worker thread, so the synchronous file object (and
    # the database and disk calls) do not block the event loop.
    while chunk := file.file.read(READ_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLarge(f"The file exceeds {max_bytes // (1024 * 1024)} MB.")
        parts.append(chunk)
    return b"".join(parts)


def sanitize_filename(raw: str | None) -> str:
    """Keep only the base name, without control characters, at most 200 characters.

    The name is only displayed (files are stored by document id), but it must not carry
    a path or characters that break a header or the UI.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()
    if len(name) > MAX_FILENAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        # Keep a short extension (".pdf") when cutting a long name.
        if dot and len(ext) <= 10:
            name = stem[: MAX_FILENAME_CHARS - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILENAME_CHARS]
    return name or DEFAULT_FILENAME


def _find_live_duplicate(session: Session, tenant_id: uuid.UUID, sha256: str) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.sha256 == sha256,
            Document.status != DocumentStatus.FAILED,
        )
    )


def _duplicate(response: Response, existing: Document, tenant_id: uuid.UUID) -> UploadResponse:
    response.status_code = 200
    logger.info(
        "documents.duplicate",
        extra={"tenant_id": str(tenant_id), "document_id": str(existing.id)},
    )
    return UploadResponse(document_id=existing.id, status=existing.status, duplicate=True)


def _violated_constraint(exc: IntegrityError) -> str | None:
    # psycopg exposes the constraint (or unique index) name in the error diagnostics.
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _mark_enqueue_failed(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """A document nobody will process must not stay `pending` forever: mark it `failed`
    (a failed document does not block uploading the same file again)."""
    try:
        with tenant_transaction(factory, tenant_id) as session:
            session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.tenant_id == tenant_id,
                    Document.status == DocumentStatus.PENDING,
                )
                .values(
                    status=DocumentStatus.FAILED,
                    failure_code=FailureCode.INTERNAL_ERROR,
                    failure_detail=ENQUEUE_FAILED_DETAIL,
                    updated_at=func.now(),
                )
            )
    except Exception:
        # The database is probably down too; the 503 is still the right answer.
        logger.exception(
            "documents.mark_failed_error",
            extra={"tenant_id": str(tenant_id), "document_id": str(document_id)},
        )


# --- Reads ------------------------------------------------------------------------------


@router.get("", response_model=DocumentList)
def list_documents(
    tenant: CurrentTenant,
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    ordering: DocumentOrdering = "-created_at",
    search: Annotated[str | None, Query(max_length=200)] = None,
    status: DocumentStatusOut | None = None,
) -> DocumentList:
    """The tenant's documents, paginated. `search` matches the filename (case-insensitive)."""
    query = select(Document).where(Document.tenant_id == tenant.id)
    if search:
        query = query.where(Document.filename.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if status:
        query = query.where(Document.status == status)

    count = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    documents = session.scalars(_order(query, ordering).limit(limit).offset(offset)).all()
    return DocumentList(
        count=count, limit=limit, offset=offset, results=[to_document_out(d) for d in documents]
    )


# Declared before `/{document_id}` so "stats" is not parsed as a document id.
@router.get("/stats", response_model=DocumentStats)
def document_stats(tenant: CurrentTenant, session: TenantSession) -> DocumentStats:
    rows = session.execute(
        select(Document.status, func.count())
        .where(Document.tenant_id == tenant.id)
        .group_by(Document.status)
    ).all()
    by_status = {status: count for status, count in rows}
    return DocumentStats(total=sum(by_status.values()), by_status=StatusCounts(**by_status))


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID, tenant: CurrentTenant, session: TenantSession
) -> DocumentOut:
    return to_document_out(_get_document_or_404(session, tenant.id, document_id))


@router.get(
    "/{document_id}/file",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The PDF"}},
)
def get_document_file(
    document_id: uuid.UUID,
    tenant: CurrentTenant,
    session: TenantSession,
    storage: Annotated[Storage, Depends(get_storage)],
) -> Response:
    document = _get_document_or_404(session, tenant.id, document_id)
    if document.storage_key is None:
        raise NotFound("Document file not found.")
    try:
        data = storage.read(document.storage_key)
    except FileNotFoundError:
        logger.warning(
            "documents.file_missing",
            extra={"tenant_id": str(tenant.id), "document_id": str(document_id)},
        )
        raise NotFound("Document file not found.") from None
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(document.filename)},
    )


@router.get("/{document_id}/chunks/{chunk_id}", response_model=ChunkOut)
def get_chunk(
    document_id: uuid.UUID, chunk_id: uuid.UUID, tenant: CurrentTenant, session: TenantSession
) -> ChunkOut:
    chunk = session.scalar(
        select(Chunk).where(
            Chunk.id == chunk_id,
            Chunk.document_id == document_id,
            Chunk.tenant_id == tenant.id,
        )
    )
    if chunk is None:
        raise NotFound("Chunk not found.")
    return ChunkOut(
        id=chunk.id,
        document_id=chunk.document_id,
        page=chunk.page,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        bbox=chunk.bbox,
        page_width=chunk.page_width,
        page_height=chunk.page_height,
    )


# --- Helpers ----------------------------------------------------------------------------


def _get_document_or_404(
    session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    tracing.set_attributes(**{tracing.DOCUMENT_ID: str(document_id)})
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    if document is None:
        raise NotFound("Document not found.")
    return document


def to_document_out(document: Document) -> DocumentOut:
    metrics = None
    # Metrics exist only after a successful ingestion; all four are written together.
    if (
        document.processing_ms is not None
        and document.embedding_calls is not None
        and document.embedding_tokens is not None
        and document.embedding_tokens_estimated is not None
    ):
        metrics = DocumentMetrics(
            processing_ms=document.processing_ms,
            embedding_calls=document.embedding_calls,
            embedding_tokens=document.embedding_tokens,
            tokens_estimated=document.embedding_tokens_estimated,
        )
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        status=document.status,
        failure_code=document.failure_code,
        failure_detail=document.failure_detail,
        page_count=document.page_count,
        chunk_count=document.chunk_count,
        embedding_model=document.embedding_model,
        metrics=metrics,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _escape_like(text: str) -> str:
    """Make `%` and `_` in the user's search literal (with `\\` as the escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _order(query: Select[tuple[Document]], ordering: str) -> Select[tuple[Document]]:
    column = _ORDERING_COLUMNS[ordering.lstrip("-")]
    primary = column.desc() if ordering.startswith("-") else column.asc()
    # `id` as a tie-breaker keeps pages stable when many rows share the same value.
    return query.order_by(primary, Document.id)


def content_disposition(filename: str) -> str:
    """`inline` with an ASCII fallback name and the exact UTF-8 name (RFC 6266 / 5987)."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    ascii_name = ascii_name.replace('"', "_").replace("\\", "_")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"

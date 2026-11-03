"""The initial migration's schema: tenant isolation in the database itself
(RLS and composite FKs) and English full-text search."""

import hashlib
import uuid

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import SCHEMA_EMBEDDING_DIM
from app.db.models import Chunk, Document
from app.db.session import tenant_transaction


def _document(tenant_id: uuid.UUID, name: str = "contract.pdf") -> Document:
    return Document(
        tenant_id=tenant_id,
        filename=name,
        sha256=hashlib.sha256(f"{tenant_id}{name}".encode()).hexdigest(),
    )


def _chunk(document: Document, tenant_id: uuid.UUID, content: str) -> Chunk:
    return Chunk(
        document_id=document.id,
        tenant_id=tenant_id,
        page=1,
        chunk_index=0,
        content=content,
        embedding=[0.0] * SCHEMA_EMBEDDING_DIM,
    )


def _count_documents(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Document))


def test_tenant_sees_only_its_own_documents(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        session.add(_document(tenant_a))

    with tenant_transaction(app_sessions, tenant_a) as session:
        assert _count_documents(session) == 1
    # No WHERE tenant_id at all: the RLS policy does the filtering.
    with tenant_transaction(app_sessions, tenant_b) as session:
        assert _count_documents(session) == 0


def test_without_tenant_nothing_is_visible(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, _ = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        session.add(_document(tenant_a))

    # A session that forgets to set the tenant sees nothing: it fails closed.
    with app_sessions() as session:
        assert _count_documents(session) == 0


def test_cannot_write_rows_of_another_tenant(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, tenant_b = two_tenants
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_a) as session:
            session.add(_document(tenant_b))


def test_chunk_cannot_belong_to_another_tenant_than_its_document(owner_engine: Engine, two_tenants):
    tenant_a, tenant_b = two_tenants
    # As the owner, which bypasses RLS: what is tested is the composite FK.
    with Session(owner_engine, expire_on_commit=False) as session, session.begin():
        document = _document(tenant_a)
        session.add(document)
    with pytest.raises(IntegrityError, match="fk_chunks_document_id_tenant_id_documents"):  # noqa: SIM117
        with Session(owner_engine) as session, session.begin():
            session.add(_chunk(document, tenant_b, "text"))


def test_full_text_search_uses_english_stemming(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, _ = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        document = _document(tenant_a)
        session.add(document)
        session.flush()
        session.add(
            _chunk(document, tenant_a, "The carrier shall pay penalties for late deliveries.")
        )

    with tenant_transaction(app_sessions, tenant_a) as session:
        # "penalty" and "delivery" only match "penalties" and "deliveries" with
        # English stemming.
        hits = session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.tsv.op("@@")(text("to_tsquery('english', 'penalty & delivery')")))
        )
    assert hits == 1


def test_same_pdf_cannot_be_live_twice(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, _ = two_tenants
    with pytest.raises(IntegrityError, match="uq_documents_tenant_id_sha256_live"):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_a) as session:
            session.add(_document(tenant_a))
            session.add(_document(tenant_a))


def test_failed_pdf_can_be_uploaded_again(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, _ = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        failed = _document(tenant_a)
        failed.status = "failed"
        failed.failure_code = "corrupt_pdf"
        session.add(failed)
    with tenant_transaction(app_sessions, tenant_a) as session:
        session.add(_document(tenant_a))

    with tenant_transaction(app_sessions, tenant_a) as session:
        assert _count_documents(session) == 2


def test_same_pdf_in_two_tenants_is_allowed(app_sessions: sessionmaker[Session], two_tenants):
    tenant_a, tenant_b = two_tenants
    sha = _document(tenant_a).sha256
    for tenant_id in (tenant_a, tenant_b):
        with tenant_transaction(app_sessions, tenant_id) as session:
            document = _document(tenant_id)
            document.sha256 = sha
            session.add(document)


@pytest.mark.parametrize(
    ("status", "started"),
    [("processing", False), ("ready", True), ("pending", True)],
    ids=["processing-without-lease", "ready-with-lease", "pending-with-lease"],
)
def test_lease_only_exists_while_processing(
    app_sessions: sessionmaker[Session], two_tenants, status: str, started: bool
):
    # Entering `processing` requires setting the lease, and leaving it requires clearing
    # it. So a `processing` document always has a timestamp to decide if it expired.
    tenant_a, _ = two_tenants
    document = _document(tenant_a)
    document.status = status
    document.processing_started_at = func.now() if started else None
    with pytest.raises(IntegrityError, match="ck_documents_processing_lease"):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_a) as session:
            session.add(document)


@pytest.mark.parametrize(
    ("status", "code", "constraint"),
    [
        ("failed", None, "ck_documents_failure_status"),
        ("ready", "corrupt_pdf", "ck_documents_failure_status"),
        ("failed", "something_else", "ck_documents_failure_code"),
    ],
    ids=["failed-without-code", "ready-with-code", "unknown-code"],
)
def test_failure_code_is_closed_and_only_on_failed_documents(
    app_sessions: sessionmaker[Session], two_tenants, status: str, code: str | None, constraint: str
):
    # A failed document always says why, with a code from a closed set.
    tenant_a, _ = two_tenants
    document = _document(tenant_a)
    document.status = status
    document.failure_code = code
    with pytest.raises(IntegrityError, match=constraint):  # noqa: SIM117
        with tenant_transaction(app_sessions, tenant_a) as session:
            session.add(document)

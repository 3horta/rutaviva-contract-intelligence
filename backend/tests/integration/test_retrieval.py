"""Hybrid search against the real Postgres: isolation, document filters and ranking."""

import hashlib
import uuid

import pytest
from sqlalchemy import Engine, func
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Chunk, Document, DocumentStatus
from app.db.session import tenant_transaction
from app.providers.embeddings.fake import FakeEmbeddings
from app.retrieval.hybrid import RetrievedChunk, search
from app.retrieval.rerank import CrossEncoderReranker, Reranker

EMBEDDINGS = FakeEmbeddings(768)

# The same paragraph is indexed for both tenants (as in the sample contracts).
SHARED = (
    "Incident Reporting Protocol RV-IRP-7. Supplier shall report any accident, cargo theft "
    "or roadside breakdown to the Network Operations Center within two (2) hours."
)
PM_A = (
    "3.1 Preventive maintenance. Supplier shall perform a PM-A service on each tractor "
    "every 25,000 miles or ninety (90) days, whichever comes first."
)
FUEL = "2.1 Price. Diesel is priced at the EIA weekly index less a discount of $0.12 per gallon."
DETENTION = "2.3 Detention. Carrier may charge $75.00 per hour after two hours of free time."


def add_document(
    session: Session,
    tenant_id: uuid.UUID,
    filename: str,
    pages: list[str],
    *,
    status: DocumentStatus = DocumentStatus.READY,
    embedding_model: str = EMBEDDINGS.model_id,
) -> Document:
    """A document with one chunk per page, embedded with the fake embeddings."""
    document = Document(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        filename=filename,
        sha256=hashlib.sha256(f"{tenant_id}{filename}".encode()).hexdigest(),
        status=status,
        embedding_model=embedding_model,
    )
    if status == DocumentStatus.PROCESSING:
        # The schema requires a lease while processing.
        document.processing_started_at = func.now()
    session.add(document)
    session.flush()
    for index, (content, vector) in enumerate(
        zip(pages, EMBEDDINGS.embed_documents(pages), strict=True)
    ):
        session.add(
            Chunk(
                document_id=document.id,
                tenant_id=tenant_id,
                page=index + 1,
                chunk_index=index,
                content=content,
                embedding=vector,
            )
        )
    session.flush()
    return document


@pytest.fixture
def indexed(app_sessions: sessionmaker[Session], two_tenants):
    """Tenant A: maintenance (PM-A p1, shared paragraph p2) and fuel. Tenant B: carrier
    agreement with the same shared paragraph."""
    tenant_a, tenant_b = two_tenants
    with tenant_transaction(app_sessions, tenant_a) as session:
        maintenance = add_document(session, tenant_a, "maintenance.pdf", [PM_A, SHARED])
        fuel = add_document(session, tenant_a, "fuel.pdf", [FUEL])
    with tenant_transaction(app_sessions, tenant_b) as session:
        carrier = add_document(session, tenant_b, "carrier.pdf", [DETENTION, SHARED])
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "maintenance": maintenance.id,
        "fuel": fuel.id,
        "carrier": carrier.id,
    }


def _search(
    session: Session, tenant_id: uuid.UUID, question: str, reranker: Reranker | None = None
) -> list[RetrievedChunk]:
    return search(
        session,
        tenant_id=tenant_id,
        query_vector=EMBEDDINGS.embed_query(question),
        question=question,
        embedding_model=EMBEDDINGS.model_id,
        settings=Settings(_env_file=None),
        reranker=reranker,
    )


def test_shared_paragraph_only_returns_the_own_tenant_copy(app_sessions, indexed) -> None:
    question = "What does Incident Reporting Protocol RV-IRP-7 require?"
    with tenant_transaction(app_sessions, indexed["tenant_a"]) as session:
        results_a = _search(session, indexed["tenant_a"], question)
    with tenant_transaction(app_sessions, indexed["tenant_b"]) as session:
        results_b = _search(session, indexed["tenant_b"], question)

    assert results_a and results_b
    assert {r.document_id for r in results_a} <= {indexed["maintenance"], indexed["fuel"]}
    assert {r.document_id for r in results_b} == {indexed["carrier"]}
    assert results_a[0].document_id == indexed["maintenance"] and results_a[0].page == 2
    assert results_b[0].document_id == indexed["carrier"] and results_b[0].page == 2


def test_tenant_filter_holds_even_without_rls(owner_engine: Engine, indexed) -> None:
    # The owner role bypasses RLS: only the query's own `tenant_id` filter is left.
    with Session(owner_engine) as session:
        results = _search(session, indexed["tenant_a"], "Incident Reporting Protocol RV-IRP-7")
    assert results
    assert indexed["carrier"] not in {r.document_id for r in results}


def test_known_question_finds_the_right_document_and_page(app_sessions, indexed) -> None:
    question = "How often must the Supplier perform a PM-A service on each tractor?"
    with tenant_transaction(app_sessions, indexed["tenant_a"]) as session:
        results = _search(session, indexed["tenant_a"], question)
    top = results[0]
    assert (top.document_id, top.page, top.filename) == (
        indexed["maintenance"],
        1,
        "maintenance.pdf",
    )
    # Found by both branches: semantically close and sharing words.
    assert top.vec_rank == 1 and top.fts_rank == 1
    assert top.found_by_both
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_processing_and_other_model_documents_are_never_returned(app_sessions, indexed) -> None:
    tenant_a = indexed["tenant_a"]
    tire = "Frontera shall pay a tire delivery penalty of 1.5% per business day."
    with tenant_transaction(app_sessions, tenant_a) as session:
        processing = add_document(
            session, tenant_a, "processing.pdf", [tire], status=DocumentStatus.PROCESSING
        )
        other_model = add_document(
            session, tenant_a, "old_model.pdf", [tire], embedding_model="other-model@768"
        )
    with tenant_transaction(app_sessions, tenant_a) as session:
        results = _search(session, tenant_a, "tire delivery penalty")
    returned = {r.document_id for r in results}
    assert processing.id not in returned
    assert other_model.id not in returned


def test_question_without_usable_words_uses_only_the_vector_branch(app_sessions, indexed) -> None:
    with tenant_transaction(app_sessions, indexed["tenant_a"]) as session:
        results = _search(session, indexed["tenant_a"], "what is the & | !")
    # No tsquery: nothing comes from full text, and the query does not fail.
    assert all(r.fts_rank is None for r in results)


def test_tenant_without_documents_gets_nothing(app_sessions, indexed) -> None:
    empty_tenant = uuid.uuid4()  # never inserted: there cannot be rows for it
    with tenant_transaction(app_sessions, indexed["tenant_a"]) as session:
        assert _search(session, empty_tenant, "Incident Reporting Protocol RV-IRP-7") == []


class FuelFirstScorer:
    """A stand-in cross-encoder that prefers the fuel clause, whatever the question, and
    records every passage it was shown."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def predict(self, pairs: list[tuple[str, str]], **kwargs: object) -> list[float]:
        self.seen.extend(passage for _, passage in pairs)
        return [10.0 if passage == FUEL else 0.0 for _, passage in pairs]


def test_reranker_reorders_only_the_tenants_own_candidates(app_sessions, indexed) -> None:
    question = "What does Incident Reporting Protocol RV-IRP-7 require?"
    scorer = FuelFirstScorer()
    reranker = CrossEncoderReranker(scorer, "stub")
    with tenant_transaction(app_sessions, indexed["tenant_a"]) as session:
        plain = _search(session, indexed["tenant_a"], question)
        reranked = _search(session, indexed["tenant_a"], question, reranker)

    # Without reranking the shared paragraph wins; the reranker moves fuel to the top.
    assert plain[0].content == SHARED and plain[0].rerank_score is None
    assert reranked[0].document_id == indexed["fuel"] and reranked[0].rerank_score == 10.0
    assert {c.chunk_id for c in reranked} == {c.chunk_id for c in plain}  # reorder only
    # It only ever saw tenant A's chunks: DETENTION exists only in tenant B.
    assert scorer.seen and DETENTION not in scorer.seen

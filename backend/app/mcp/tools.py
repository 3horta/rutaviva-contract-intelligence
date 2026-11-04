"""What the MCP tools do: plain functions over a session already scoped to the tenant.

They are the same operations the REST API and the chat use (hybrid retrieval, the
validated answer pipeline), not a second implementation: this module only shapes their
results for a client model. The MCP layer (`server.py`) authenticates, opens the tenant
transaction and runs these in a worker thread, because they are blocking.

Every query filters by `tenant_id` explicitly, and the session runs with the tenant set
for Row Level Security, as everywhere else (ADR-002).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFound
from app.db.models import Chunk, Document
from app.generation.service import answer_question
from app.mcp.schemas import (
    WITHHELD_TEXT,
    AskResult,
    DocumentsResult,
    DocumentSummary,
    Passage,
    PassageResult,
    SearchHit,
    SearchResult,
)
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import LLMProvider
from app.retrieval.hybrid import RetrievedChunk, search
from app.retrieval.judge import RelevanceJudge
from app.schemas.documents import DocumentStatusOut
from app.schemas.query import FoundBy

# Neighbours on each side that `get_passage` may return.
MAX_NEIGHBOURS = 3


def list_documents(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    status: DocumentStatusOut | None,
    filename_contains: str | None,
    limit: int,
) -> DocumentsResult:
    query = select(Document).where(Document.tenant_id == tenant_id)
    if status:
        query = query.where(Document.status == status)
    if filename_contains:
        query = query.where(
            func.strpos(func.lower(Document.filename), filename_contains.lower()) > 0
        )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    documents = session.scalars(
        query.order_by(Document.created_at.desc(), Document.id).limit(limit)
    ).all()
    return DocumentsResult(
        total=total,
        documents=[
            DocumentSummary(
                document_id=d.id,
                filename=d.filename,
                status=d.status,  # type: ignore[arg-type]  # checked by the DB constraint
                pages=d.page_count,
                chunks=d.chunk_count,
                uploaded_at=d.created_at,
            )
            for d in documents
        ],
    )


def search_contracts(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    query: str,
    k: int,
    settings: Settings,
    embeddings: EmbeddingsProvider,
) -> SearchResult:
    """The app's search (vector + full text, RRF, then the cross-encoder reranker when it
    is on), best first: at most `k` hits, never more than `retrieval_k`. No relevance
    judge and no LLM: the client model reads the passages and decides."""
    chunks = search(
        session,
        tenant_id=tenant_id,
        query_vector=embeddings.embed_query(query),
        question=query,
        embedding_model=embeddings.model_id,
        settings=settings,
    )
    return SearchResult(query=query, hits=[_hit(chunk) for chunk in chunks[:k]])


def get_passage(
    session: Session, tenant_id: uuid.UUID, *, chunk_id: uuid.UUID, neighbours: int
) -> PassageResult:
    """A passage and up to `neighbours` passages on each side, from the same document."""
    target = session.scalar(select(Chunk).where(Chunk.id == chunk_id, Chunk.tenant_id == tenant_id))
    if target is None:
        raise NotFound("No passage with that id in your documents.")
    rows = session.execute(
        select(Chunk, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.tenant_id == tenant_id,
            Chunk.document_id == target.document_id,
            Chunk.chunk_index.between(
                target.chunk_index - neighbours, target.chunk_index + neighbours
            ),
        )
        .order_by(Chunk.chunk_index)
    ).all()
    return PassageResult(
        document_id=target.document_id,
        filename=rows[0].filename,
        requested_chunk_id=chunk_id,
        passages=[
            Passage(
                chunk_id=chunk.id,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                flagged_injection=chunk.flagged_injection,
                text=WITHHELD_TEXT if chunk.flagged_injection else chunk.content,
            )
            for chunk, _ in rows
        ],
    )


def ask(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    question: str,
    settings: Settings,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    judge: RelevanceJudge,
) -> AskResult:
    """The full answer pipeline of `POST /query` (retrieval, relevance judge, LLM,
    citation validation, guardrails), without a conversation: nothing is stored."""
    response = answer_question(
        question,
        tenant_id=tenant_id,
        session=session,
        embeddings=embeddings,
        llm=llm,
        settings=settings,
        judge=judge,
    )
    return AskResult(
        answer=response.answer,
        status=response.status,
        confidence=response.confidence,
        citations=response.citations,
        warnings=response.warnings,
        trace_id=response.details.trace_id if response.details else None,
    )


def _hit(chunk: RetrievedChunk) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        filename=chunk.filename,
        page=chunk.page,
        score=round(chunk.score, 6),
        rerank_score=None if chunk.rerank_score is None else round(chunk.rerank_score, 6),
        found_by=_found_by(chunk),
        flagged_injection=chunk.flagged_injection,
        text=WITHHELD_TEXT if chunk.flagged_injection else chunk.content,
    )


def _found_by(chunk: RetrievedChunk) -> FoundBy:
    if chunk.found_by_both:
        return "both"
    return "vector" if chunk.vec_rank is not None else "full_text"

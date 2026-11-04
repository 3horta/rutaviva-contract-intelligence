"""Hybrid retrieval: vector search and full-text search fused with weighted RRF.

One SQL statement with two CTEs, one per branch:
- `vec`: nearest chunks by cosine distance (`<=>`, HNSW index).
- `fts`: chunks whose `tsv` matches the OR tsquery, ranked by `ts_rank_cd` (GIN index).

Both branches give each chunk a rank (1 = best). Reciprocal Rank Fusion combines ranks,
not raw scores, because cosine distances and `ts_rank_cd` values are on unrelated scales:

    score = w_vec / (rrf_k + vec_rank) + w_fts / (rrf_k + fts_rank)

A chunk missing from a branch contributes 0 for it (FULL OUTER JOIN + COALESCE), so a
chunk found by both branches beats one found by a single branch at the same rank.

Tenant isolation: each branch filters `c.tenant_id = :tenant` itself, plus the document
being `ready` and indexed with the same embedding model as the query vector. RLS (the
session's `app.tenant_id`) is a second layer underneath, not the only one: this query
returns only the tenant's chunks even on a connection that bypasses RLS.
"""

import uuid
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.config import SCHEMA_EMBEDDING_DIM, Settings
from app.core.tracing import (
    RETRIEVAL_FTS_HITS,
    RETRIEVAL_K,
    RETRIEVAL_RESULTS,
    RETRIEVAL_VEC_HITS,
    set_attributes,
)
from app.db.models import FTS_CONFIG, DocumentStatus
from app.retrieval.ranking import select_chunks
from app.retrieval.rerank import Reranker, get_reranker
from app.retrieval.tsquery import build_or_tsquery


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int
    content: str
    flagged_injection: bool
    score: float  # fused RRF score
    vec_rank: int | None  # 1-based rank in the vector branch; None if not found there
    fts_rank: int | None  # 1-based rank in the full-text branch; None if not found there
    # Where the document's ingestion was traced, for span links from the query trace.
    ingest_trace_id: str | None = None
    ingest_span_id: str | None = None
    # Cross-encoder score (app/retrieval/rerank.py); None when the RRF order was kept.
    rerank_score: float | None = None

    @property
    def found_by_both(self) -> bool:
        return self.vec_rank is not None and self.fts_rank is not None


# Note on HNSW and the tenant filter: an HNSW index scan returns the nearest neighbours
# first and the WHERE clause is applied afterwards (post-filtering). With many tenants
# the index may hand back `hnsw.ef_search` (default 40) neighbours that mostly belong to
# other tenants, so the vector branch can return FEWER than `:candidates` rows even when
# the tenant has more chunks. It never returns another tenant's rows, only fewer.
# TODO(isolation milestone): measure with EXPLAIN (also whether RLS blocks the index,
# ADR-002) and choose between pgvector's iterative scans (`hnsw.iterative_scan`), a
# higher `ef_search`, or per-tenant partial indexes.
_HYBRID_SQL = text(
    f"""
WITH
query AS (
    -- NULL when the question had no usable words: `tsv @@ NULL` matches nothing, so
    -- the fts branch is empty and only the vector branch counts.
    SELECT to_tsquery('{FTS_CONFIG}', CAST(:tsquery AS text)) AS tsq
),
vec AS (
    SELECT c.id,
           row_number() OVER (ORDER BY c.embedding <=> :qvec, c.id) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
    WHERE c.tenant_id = :tenant
      AND d.status = :ready
      AND d.embedding_model = :model
    ORDER BY c.embedding <=> :qvec, c.id
    LIMIT :candidates
),
fts AS (
    SELECT c.id,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, query.tsq) DESC, c.id) AS rank
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
    CROSS JOIN query
    WHERE c.tenant_id = :tenant
      AND d.status = :ready
      AND d.embedding_model = :model
      AND c.tsv @@ query.tsq
    ORDER BY ts_rank_cd(c.tsv, query.tsq) DESC, c.id
    LIMIT :candidates
),
fused AS (
    SELECT COALESCE(vec.id, fts.id) AS id,
           vec.rank AS vec_rank,
           fts.rank AS fts_rank,
           COALESCE(CAST(:w_vec AS float8) / (:rrf_k + vec.rank), 0)
         + COALESCE(CAST(:w_fts AS float8) / (:rrf_k + fts.rank), 0) AS score
    FROM vec
    FULL OUTER JOIN fts ON fts.id = vec.id
)
SELECT f.id AS chunk_id, c.document_id, d.filename, d.ingest_trace_id, d.ingest_span_id,
       c.page, c.content,
       c.flagged_injection, f.score, f.vec_rank, f.fts_rank
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
ORDER BY f.score DESC, f.id
"""
).bindparams(bindparam("qvec", type_=Vector(SCHEMA_EMBEDDING_DIM)))


def search(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    query_vector: list[float],
    question: str,
    embedding_model: str,
    settings: Settings,
    reranker: Reranker | None = None,
) -> list[RetrievedChunk]:
    """Hybrid top-k chunks of the tenant for the question, best first.

    The SQL returns every fused candidate (at most 2 x `retrieval_candidates`, so it
    stays small); the reranker (`RERANKER`, or `reranker` to override it) reorders them;
    `select_chunks` then drops near-duplicates, caps chunks per document and keeps the
    top `retrieval_k` of that order. There is no score threshold: deciding that the
    evidence is not enough is the job of the answer step (see `generation/service.py`).
    """
    rows = session.execute(
        _HYBRID_SQL,
        {
            "tsquery": build_or_tsquery(question),
            "qvec": query_vector,
            "tenant": tenant_id,
            "ready": DocumentStatus.READY.value,
            "model": embedding_model,
            "candidates": settings.retrieval_candidates,
            "rrf_k": settings.rrf_k,
            "w_vec": settings.rrf_weight_vector,
            "w_fts": settings.rrf_weight_fts,
        },
    ).all()
    candidates = [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            document_id=row.document_id,
            filename=row.filename,
            page=row.page,
            content=row.content,
            flagged_injection=row.flagged_injection,
            score=float(row.score),
            vec_rank=row.vec_rank,
            fts_rank=row.fts_rank,
            ingest_trace_id=row.ingest_trace_id,
            ingest_span_id=row.ingest_span_id,
        )
        for row in rows
    ]
    candidates = (reranker or get_reranker(settings)).rerank(question, candidates)
    results = select_chunks(
        candidates, k=settings.retrieval_k, max_per_document=settings.max_chunks_per_document
    )
    # Counts only, on the caller's span (`query.retrieve`): never chunk content.
    set_attributes(
        **{
            RETRIEVAL_K: settings.retrieval_k,
            RETRIEVAL_VEC_HITS: sum(1 for c in candidates if c.vec_rank is not None),
            RETRIEVAL_FTS_HITS: sum(1 for c in candidates if c.fts_rank is not None),
            RETRIEVAL_RESULTS: len(results),
        }
    )
    return results

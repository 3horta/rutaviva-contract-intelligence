"""Reranking: after the hybrid search fuses its two branches, a cross-encoder reads each
(question, chunk) pair together and scores how well the chunk answers the question.

Why: the vector branch compares two vectors computed separately (the question's and the
chunk's), and the full-text branch counts shared words. Neither reads the question and
the chunk together, so a clause that only *mentions* the question's words ("late
delivery" in a notice clause) can outrank the clause that answers it (the penalty). A
cross-encoder attends over both texts at once and tells them apart. It is too slow to
run over a whole corpus, so it only reorders the fused candidates (at most
2 x `RETRIEVAL_CANDIDATES`); `select_chunks` then keeps the top `RETRIEVAL_K` of the
new order, with the same duplicate and per-document rules.

Where it sits:

    hybrid SQL (vector + full text, tenant-filtered) -> RRF fusion
      -> rerank (this module) -> select_chunks -> relevance judge (real LLM only) -> LLM

The tenant filter is already applied by the SQL: the reranker only sees the tenant's
own chunks. It can only REORDER: it never adds or removes a candidate.

`RERANKER=cross_encoder` (default): `cross-encoder/ms-marco-MiniLM-L-6-v2`, ~90 MB, run
locally on the CPU, baked into the image (no network, no key). `RERANKER=none` keeps
the RRF order (tests, and a fallback switch).

If the model fails, the RRF order is used unchanged (`retrieval.rerank_failed` in the
logs, `retrieval.rerank.failed` on the span): a reranker problem never makes a query
fail.
"""

import dataclasses
import logging
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from app.core import metrics
from app.core.config import Settings
from app.core.tracing import (
    RERANK_CANDIDATES,
    RERANK_FAILED,
    RERANK_LATENCY_MS,
    RERANK_MODEL,
    set_attributes,
    span,
)

if TYPE_CHECKING:  # only for the type hints; avoids a circular import with hybrid.py
    from app.retrieval.hybrid import RetrievedChunk

logger = logging.getLogger(__name__)

# The model reads at most 512 tokens per pair; a chunk is ~1,000 characters (~250
# tokens), so this only guards against an unusually long chunk.
MAX_CHUNK_CHARS = 2000
# Pairs per forward pass: all the candidates of one question fit in a few batches.
BATCH_SIZE = 16


class PairScorer(Protocol):
    """The part of a cross-encoder the reranker uses: one score per (query, passage)."""

    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> Sequence[float]: ...


class Reranker(Protocol):
    def rerank(self, question: str, candidates: list["RetrievedChunk"]) -> list["RetrievedChunk"]:
        """Return the candidates with `rerank_score` set, best first. Never raises: on
        any failure, return them unchanged (RRF order, no `rerank_score`)."""
        ...


class NoRerank:
    """`RERANKER=none`: the RRF order is kept."""

    model_id = "none"

    def rerank(self, question: str, candidates: list["RetrievedChunk"]) -> list["RetrievedChunk"]:
        return candidates


class CrossEncoderReranker:
    def __init__(self, scorer: PairScorer, model_id: str) -> None:
        self.scorer = scorer
        self.model_id = model_id

    def rerank(self, question: str, candidates: list["RetrievedChunk"]) -> list["RetrievedChunk"]:
        if len(candidates) < 2:
            return candidates  # nothing to reorder: no model call
        with span(
            "retrieval.rerank", **{RERANK_MODEL: self.model_id, RERANK_CANDIDATES: len(candidates)}
        ) as rerank_span:
            started = time.perf_counter()
            try:
                scores = self._scores(question, candidates)
            except Exception as exc:  # model errors, wrong shape: all the same
                set_attributes(rerank_span, **{RERANK_FAILED: True})
                metrics.record_rerank_failure()
                logger.warning(
                    "retrieval.rerank_failed",
                    extra={"error_type": type(exc).__name__, "candidates": len(candidates)},
                )
                return candidates
            latency_ms = round((time.perf_counter() - started) * 1000)
            set_attributes(rerank_span, **{RERANK_FAILED: False, RERANK_LATENCY_MS: latency_ms})

        scored = [
            dataclasses.replace(chunk, rerank_score=score)
            for chunk, score in zip(candidates, scores, strict=True)
        ]
        # Stable sort: equal scores keep the RRF order.
        return sorted(scored, key=lambda chunk: chunk.rerank_score, reverse=True)

    def _scores(self, question: str, candidates: list["RetrievedChunk"]) -> list[float]:
        pairs = [(question, chunk.content[:MAX_CHUNK_CHARS]) for chunk in candidates]
        raw = self.scorer.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False)
        scores = [float(value) for value in raw]
        if len(scores) != len(candidates):
            raise ValueError(f"{len(scores)} scores for {len(candidates)} candidates")
        return scores


# Loading the model takes seconds: exactly once per process, and a lock so two
# concurrent first requests do not both load it (same pattern as the local embeddings).
_lock = threading.Lock()
_instances: dict[str, CrossEncoderReranker] = {}


def _cross_encoder(model_name: str) -> CrossEncoderReranker:
    with _lock:
        if model_name not in _instances:
            try:
                # Imported here so `RERANKER=none` (tests, macOS dev) never loads PyTorch.
                from sentence_transformers import CrossEncoder
            except ImportError as exc:  # pragma: no cover - depends on the platform
                raise RuntimeError(
                    "RERANKER=cross_encoder needs the 'ml' dependency group (installed in "
                    "the Docker image). For local development without it use RERANKER=none."
                ) from exc
            scorer = CrossEncoder(model_name, device="cpu", max_length=512)
            _instances[model_name] = CrossEncoderReranker(scorer, model_name.rsplit("/", 1)[-1])
        return _instances[model_name]


def get_reranker(settings: Settings) -> Reranker:
    if settings.reranker == "none":
        return NoRerank()
    return _cross_encoder(settings.reranker_model)

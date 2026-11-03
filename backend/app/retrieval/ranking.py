"""Post-processing of the fused candidates: no duplicates, no single document dominating.

Both rules use the space in the LLM context better:
- Near-duplicates: the same paragraph can be indexed twice (chunk overlap at a boundary,
  a clause repeated in an exhibit, the same text in two documents). Sending it twice
  adds tokens and no evidence. Similarity is word-set overlap (Jaccard), which is cheap,
  deterministic and easy to explain; the first (best-scored) copy is kept.
- Per-document cap: a long document with many similar chunks would otherwise push out
  other documents, e.g. the SLA exhibit that contradicts the main agreement.
"""

import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for the type hints; avoids a circular import with hybrid.py
    from app.retrieval.hybrid import RetrievedChunk

# Two chunks sharing at least this fraction of their distinct words are duplicates.
NEAR_DUPLICATE_JACCARD = 0.9

_WORD = re.compile(r"[a-z0-9]+")


def _words(content: str) -> frozenset[str]:
    return frozenset(_WORD.findall(content.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def select_chunks(
    candidates: Sequence["RetrievedChunk"], *, k: int, max_per_document: int
) -> list["RetrievedChunk"]:
    """Walk the candidates best first and keep up to `k`, skipping near-duplicates of a
    chunk already kept and chunks of a document that already has `max_per_document`."""
    kept: list[RetrievedChunk] = []
    kept_words: list[frozenset[str]] = []
    per_document: dict[uuid.UUID, int] = {}

    for chunk in sorted(candidates, key=lambda c: c.score, reverse=True):
        if len(kept) == k:
            break
        if per_document.get(chunk.document_id, 0) >= max_per_document:
            continue
        words = _words(chunk.content)
        if any(_jaccard(words, other) >= NEAR_DUPLICATE_JACCARD for other in kept_words):
            continue
        kept.append(chunk)
        kept_words.append(words)
        per_document[chunk.document_id] = per_document.get(chunk.document_id, 0) + 1
    return kept

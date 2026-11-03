"""Post-processing of the fused candidates: no duplicates, no single document dominating.

Both rules use the space in the LLM context better:
- Near-duplicates WITHIN one document: the same text can be indexed twice (chunk overlap
  at a boundary, a clause repeated on another page). Sending it twice adds tokens and no
  evidence. Similarity is word-set overlap (Jaccard): cheap, deterministic, easy to
  explain; the best-scored copy is kept. The same text in two DIFFERENT documents is
  kept on purpose: a standard clause present in two contracts is information the user
  needs (both contracts are cited).
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
    chunk already kept from the same document, and chunks of a document that already
    has `max_per_document`."""
    kept: list[RetrievedChunk] = []
    kept_words: dict[uuid.UUID, list[frozenset[str]]] = {}  # per document
    per_document: dict[uuid.UUID, int] = {}

    for chunk in sorted(candidates, key=lambda c: c.score, reverse=True):
        if len(kept) == k:
            break
        if per_document.get(chunk.document_id, 0) >= max_per_document:
            continue
        words = _words(chunk.content)
        same_document = kept_words.setdefault(chunk.document_id, [])
        if any(_jaccard(words, other) >= NEAR_DUPLICATE_JACCARD for other in same_document):
            continue
        kept.append(chunk)
        same_document.append(words)
        per_document[chunk.document_id] = per_document.get(chunk.document_id, 0) + 1
    return kept

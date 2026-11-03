"""Deterministic fake embeddings: the hashing trick over words and character trigrams.

Why this and not random vectors: similar texts must get similar vectors, so vector
search actually works in fake mode (lexically), and API and worker — different
processes — must produce the same vector for the same text. That rules out Python's
`hash()`, which is salted per process (PYTHONHASHSEED); `hashlib` is stable.
"""

import hashlib
import math
import re
import unicodedata
from collections.abc import Sequence

from app.core.config import FAKE_EMBEDDING_MODEL

_WORD = re.compile(r"[a-z0-9]+")
# Very common words carry no meaning for retrieval and would dominate short texts.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "any", "are", "as", "at", "be", "by", "each", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "shall", "that", "the",
        "this", "to", "was", "were", "will", "with",
    }
)  # fmt: skip


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _features(text: str) -> list[tuple[str, float]]:
    words = [w for w in _WORD.findall(_normalise(text)) if w not in _STOPWORDS]
    features = [(f"w:{w}", 1.0) for w in words]
    # Trigrams make "penalty"/"penalties" or "deliver"/"delivery" overlap.
    for word in words:
        padded = f"#{word}#"
        features.extend((f"t:{padded[i : i + 3]}", 0.5) for i in range(len(padded) - 2))
    return features


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    n = int.from_bytes(digest, "big")
    sign = 1.0 if (n >> 63) & 1 else -1.0
    return n % dim, sign


class FakeEmbeddings:
    tokens_estimated = True

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.model_id = f"{FAKE_EMBEDDING_MODEL}@{dim}"

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for feature, weight in _features(text):
            index, sign = _bucket(feature, self.dim)
            vector[index] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # Empty or stopword-only text: a fixed unit vector keeps pgvector's cosine
            # distance defined (a zero vector would give NaN).
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def count_tokens(self, texts: Sequence[str]) -> int:
        # Common rule of thumb: about 4 characters per token.
        return sum(math.ceil(len(text) / 4) for text in texts)

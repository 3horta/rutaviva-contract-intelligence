"""Embeddings port. Implementations: `local` (default at runtime), `fake` (tests) and
`gemini` (optional, with a key)."""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingsProvider(Protocol):
    # Stored per document (e.g. "fake-hash-v1@768"): search only compares vectors made
    # by the same model, and changing model means reindexing.
    model_id: str
    dim: int
    # True when token counts are estimated (fake provider), not reported by an API.
    tokens_estimated: bool

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One L2-normalised vector per text. Raise `TransientProviderError` (retry) or
        `PermanentProviderError` (give up)."""
        ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, texts: Sequence[str]) -> int:
        """Tokens billed (or estimated) for embedding `texts`."""
        ...

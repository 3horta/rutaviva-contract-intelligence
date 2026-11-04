"""Gemini embeddings through LiteLLM (optional, needs `GEMINI_API_KEY`).

- `gemini-embedding-001` truncated to 768 dimensions (the schema's `vector(768)`).
- Passages use the task type `RETRIEVAL_DOCUMENT`, questions `RETRIEVAL_QUERY`: Gemini
  embeds each side for its role, like BGE's query instruction.
- Truncated Gemini vectors are NOT unit length, and search uses cosine distance with
  the fake LLM's dot products: every vector is L2-normalised here.
- Errors map like the LLM's (`providers/litellm_support.py`): 429 and 5xx are transient
  (the worker retries the document), 400/401/403 are permanent.

Switching embedding model means REINDEXING: every document stores the `model_id` it
was embedded with, and search only compares vectors of the same model, so documents
indexed with another model stop being found until `scripts/reindex.py` runs.
"""

import math
from collections.abc import Sequence

from app.core.errors import PermanentProviderError
from app.providers.litellm_support import load_litellm, to_provider_error

DOCUMENT_TASK = "RETRIEVAL_DOCUMENT"
QUERY_TASK = "RETRIEVAL_QUERY"
# Gemini accepts at most 100 texts per batch request.
MAX_BATCH = 100
EMBEDDING_TIMEOUT_S = 30.0


class GeminiEmbeddings:
    # Gemini's batch embedding endpoint reports no token usage (the `usage` LiteLLM
    # fills in is its own local count), so tokens are estimated and flagged as such.
    tokens_estimated = True

    def __init__(self, model: str, api_key: str, dim: int, *, batch_size: int = 32) -> None:
        self._litellm = load_litellm()
        self._model = f"gemini/{model}"
        self._api_key = api_key
        self._batch_size = min(batch_size, MAX_BATCH)
        self.dim = dim
        # Same format as `Settings.embedding_model_id`: stored with each document.
        self.model_id = f"{model}@{dim}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(self._embed(texts[start : start + self._batch_size], DOCUMENT_TASK))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], QUERY_TASK)[0]

    def count_tokens(self, texts: Sequence[str]) -> int:
        # Rule of thumb: about 4 characters per token (see `tokens_estimated`).
        return sum(math.ceil(len(text) / 4) for text in texts)

    def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        try:
            response = self._litellm.embedding(
                model=self._model,
                input=list(texts),
                dimensions=self.dim,
                task_type=task_type,
                api_key=self._api_key,
                timeout=EMBEDDING_TIMEOUT_S,
                num_retries=0,  # the worker (documents) and the API decide on retries
            )
        except Exception as exc:
            raise to_provider_error(self._litellm, exc, "Gemini embedding") from exc

        # Items can come back in any order: put each one at its `index`.
        items = sorted(response.data, key=lambda item: item["index"])
        if len(items) != len(texts):
            raise PermanentProviderError(f"expected {len(texts)} vectors, got {len(items)}")
        return [self._normalised(item["embedding"]) for item in items]

    def _normalised(self, vector: list[float]) -> list[float]:
        if len(vector) != self.dim:
            raise PermanentProviderError(
                f"Gemini returned a {len(vector)}-dim vector; the schema expects {self.dim}"
            )
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            raise PermanentProviderError("Gemini returned a zero vector")
        return [v / norm for v in vector]

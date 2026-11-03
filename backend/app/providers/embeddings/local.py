"""Real embedding model run locally with sentence-transformers (no API, no key).

Default model: `BAAI/bge-base-en-v1.5` — 768 dimensions (the schema's), trained for
retrieval, English, MIT licence. The weights are baked into the Docker image and loaded
from the local cache (`HF_HUB_OFFLINE=1`), so nothing is downloaded at runtime.

The same model, library version and CPU give the same vector for the same text, so the
API (queries) and the worker (chunks), two containers of the same image, agree.
"""

from collections.abc import Sequence
from typing import Any

from app.core.errors import PermanentProviderError

# BGE v1.5 recommends this instruction for queries, and none for passages.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class LocalEmbeddings:
    tokens_estimated = False  # counted with the model's own tokenizer

    def __init__(self, model_name: str, dim: int, *, batch_size: int = 32) -> None:
        try:
            # Imported here so that fake mode (tests, macOS dev) never loads PyTorch.
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on the platform
            raise RuntimeError(
                "EMBEDDINGS_PROVIDER=local needs the 'ml' dependency group (installed in "
                "the Docker image). For local development without it use "
                "EMBEDDINGS_PROVIDER=fake."
            ) from exc

        self._model: Any = SentenceTransformer(model_name, device="cpu")
        actual = self._model.get_sentence_embedding_dimension()
        if actual != dim:
            raise PermanentProviderError(
                f"{model_name} produces {actual}-dim vectors; the schema expects {dim}"
            )
        self.dim = dim
        self.model_id = f"{model_name.rsplit('/', 1)[-1]}@{dim}"
        self._batch_size = batch_size

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            vectors = self._model.encode(
                list(texts),
                batch_size=self._batch_size,
                normalize_embeddings=True,  # unit length: cosine distance in pgvector
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            # A local model failing is not fixed by retrying (bad input, out of memory).
            raise PermanentProviderError(f"local embedding failed: {type(exc).__name__}") from exc
        return [vector.tolist() for vector in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([QUERY_INSTRUCTION + text])[0]

    def count_tokens(self, texts: Sequence[str]) -> int:
        tokenizer = self._model.tokenizer
        return sum(len(tokenizer.encode(text, add_special_tokens=True)) for text in texts)

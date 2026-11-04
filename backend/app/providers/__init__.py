"""Provider factories: the only place that decides which implementation is active."""

import threading

from app.core.config import Settings
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.embeddings.fake import FakeEmbeddings


def get_embeddings(settings: Settings) -> EmbeddingsProvider:
    if settings.embeddings_provider == "fake":
        return FakeEmbeddings(dim=settings.embedding_dim)
    if settings.embeddings_provider == "local":
        return _local_embeddings(
            settings.local_embedding_model, settings.embedding_dim, settings.embedding_batch_size
        )
    # Real paid provider: imported lazily so other modes never load its SDK.
    raise NotImplementedError("gemini embeddings are not wired yet")


# Loading the model takes seconds and hundreds of MB: exactly once per process. A lock,
# not just lru_cache: lru_cache does not stop two concurrent first requests (two API
# threads) from both loading the model.
_local_lock = threading.Lock()
_local_instances: dict[tuple[str, int, int], EmbeddingsProvider] = {}


def _local_embeddings(model_name: str, dim: int, batch_size: int) -> EmbeddingsProvider:
    key = (model_name, dim, batch_size)
    with _local_lock:
        if key not in _local_instances:
            from app.providers.embeddings.local import LocalEmbeddings

            _local_instances[key] = LocalEmbeddings(model_name, dim, batch_size=batch_size)
        return _local_instances[key]

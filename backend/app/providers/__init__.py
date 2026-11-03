"""Provider factories: the only place that decides which implementation is active."""

from functools import lru_cache

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


@lru_cache
def _local_embeddings(model_name: str, dim: int, batch_size: int) -> EmbeddingsProvider:
    # Loading the model takes seconds and hundreds of MB: once per process.
    from app.providers.embeddings.local import LocalEmbeddings

    return LocalEmbeddings(model_name, dim, batch_size=batch_size)

"""Provider factories: the only place that decides which implementation is active."""

from app.core.config import Settings
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.embeddings.fake import FakeEmbeddings


def get_embeddings(settings: Settings) -> EmbeddingsProvider:
    if settings.embeddings_provider == "fake":
        return FakeEmbeddings(dim=settings.embedding_dim)
    # Real provider: imported lazily so fake mode never loads its SDK.
    raise NotImplementedError("gemini embeddings are not wired yet")

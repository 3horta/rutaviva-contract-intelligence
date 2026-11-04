"""LLM provider factories: the only place that decides which implementation is active.

Keyless mode (`LLM_PROVIDER=fake`) never imports LiteLLM: the real clients are imported
inside the functions, only when `LLM_PROVIDER=litellm`.
"""

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLM
from app.providers.llm.router import Capability, model_for

if TYPE_CHECKING:
    from app.providers.llm.litellm_provider import LiteLLMJSONClient


def get_llm(settings: Settings, embeddings: EmbeddingsProvider) -> LLMProvider:
    """The model that drafts the answer. `embeddings` must be the instance retrieval
    uses: the fake LLM ranks sentences with it, and the real model is loaded only once
    per process."""
    if settings.llm_provider == "fake":
        return FakeLLM(embeddings, settings.fake_llm_mode)
    from app.providers.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(model_for(settings, "answer"), _api_key(settings))


def get_json_client(settings: Settings, capability: Capability) -> "LiteLLMJSONClient | None":
    """The JSON client for a small job (`resolve`, `judge`) on the fast model, or None in
    keyless mode, where those jobs have deterministic stand-ins (or are skipped)."""
    if settings.llm_provider == "fake":
        return None
    from app.providers.llm.litellm_provider import LiteLLMJSONClient

    return LiteLLMJSONClient(model_for(settings, capability), _api_key(settings))


def _api_key(settings: Settings) -> str:
    # Config validation guarantees the key for gemini/ models (the only ones accepted).
    assert settings.gemini_api_key is not None
    return settings.gemini_api_key.get_secret_value()

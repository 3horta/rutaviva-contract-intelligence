"""LLM provider factory: the only place that decides which implementation is active."""

from app.core.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLM


def get_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLM(settings.fake_llm_mode)
    # Real provider (LiteLLM): a later milestone. Imported lazily so fake mode never
    # loads its SDK.
    raise NotImplementedError("the litellm provider is not wired yet")

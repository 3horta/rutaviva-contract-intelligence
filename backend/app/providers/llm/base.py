"""LLM port. Implementations: `fake` (deterministic, extractive, default) and a real
provider through LiteLLM.

The prompt is passed as a structure, not a string: the real adapter renders it (with the
document content clearly delimited as data), the fake one reads it directly.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ContextChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int
    text: str
    # Flagged by the prompt-injection detector at indexing time.
    flagged_injection: bool = False


@dataclass(frozen=True)
class PromptInput:
    question: str
    chunks: list[ContextChunk]
    # Validation error of the previous attempt, fed back on retry. None on the first try.
    feedback: str | None = None
    attempt: int = 1


@dataclass(frozen=True)
class LLMResult:
    text: str  # raw model output, expected to be JSON matching `LLMAnswer`
    model: str
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool
    latency_ms: int
    extra: dict[str, str] = field(default_factory=dict)


class LLMProvider(Protocol):
    model_id: str

    def generate(self, prompt: PromptInput, *, timeout_s: float) -> LLMResult:
        """Raise `TransientProviderError` on timeouts/429/5xx and
        `PermanentProviderError` on other failures. Never return partial text."""
        ...


class JSONCompletionClient(Protocol):
    """A chat model that returns a JSON object, for the small jobs around the answer:
    resolving follow-ups and judging relevance (`LiteLLMJSONClient`)."""

    def complete_json(self, system: str, user: str, *, timeout_s: float) -> dict[str, Any]:
        """Raise a `ProviderError` on provider failures or a reply that is not a JSON
        object. Callers treat any failure as "skip this step"."""
        ...

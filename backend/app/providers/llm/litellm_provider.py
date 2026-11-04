"""Real LLM through LiteLLM (Gemini with a key). Two small clients over one call:

- `LiteLLMProvider`: the `LLMProvider` port. Drafts the answer from the rendered prompt
  (`generation/prompt.py`), asking for JSON with the `LLMAnswer` schema. The output is
  still checked by `generation/validate.py`, like the fake LLM's.
- `LiteLLMJSONClient`: the `JSONCompletionClient` the follow-up resolver and the
  relevance judge use: a system and a user message in, a JSON object out.

Errors are mapped in `providers/litellm_support.py`: timeout -> `ProviderTimeoutError`;
429/5xx/connection -> `TransientProviderError` (with Retry-After); other 4xx ->
`PermanentProviderError`; a safety block -> `ContentFilteredError`. LiteLLM's own
retries are off (`num_retries=0`): the answer service decides what is retried.

Nothing here logs prompts, answers or keys.
"""

import json
import math
import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import ContentFilteredError, PermanentProviderError
from app.generation.prompt import render_messages
from app.generation.validate import strip_code_fence
from app.providers.litellm_support import load_litellm, to_provider_error
from app.providers.llm.base import LLMResult, PromptInput
from app.schemas.query import LLMAnswer

# LiteLLM's finish reason for a reply blocked by the provider (Gemini: SAFETY,
# PROHIBITED_CONTENT...).
CONTENT_FILTER_FINISH_REASON = "content_filter"


@dataclass(frozen=True)
class _Completion:
    text: str
    model: str
    input_tokens: int | None  # None when the provider did not report usage
    output_tokens: int | None
    latency_ms: int


class _ChatModel:
    """One chat model behind LiteLLM. The key is passed per call, never set globally."""

    def __init__(self, model: str, api_key: str) -> None:
        self._litellm = load_litellm()
        self.model_id = model
        self._api_key = api_key

    def _complete(
        self, messages: list[dict[str, str]], response_format: Any, timeout_s: float
    ) -> _Completion:
        started = time.perf_counter()
        try:
            response = self._litellm.completion(
                model=self.model_id,
                messages=messages,
                response_format=response_format,
                api_key=self._api_key,
                timeout=timeout_s,
                num_retries=0,
            )
        except Exception as exc:
            raise to_provider_error(self._litellm, exc, "LLM completion") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)

        choice = response.choices[0]
        if choice.finish_reason == CONTENT_FILTER_FINISH_REASON:
            raise ContentFilteredError("LLM reply blocked by the provider's safety filter")
        usage = getattr(response, "usage", None)
        return _Completion(
            # None when the model returned no text: validation rejects "" and retries.
            text=choice.message.content or "",
            model=getattr(response, "model", None) or self.model_id,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            latency_ms=latency_ms,
        )


class LiteLLMProvider(_ChatModel):
    """`LLMProvider` for the answer (the quality model)."""

    def generate(self, prompt: PromptInput, *, timeout_s: float) -> LLMResult:
        messages = render_messages(prompt)
        # The Pydantic class: LiteLLM sends its JSON schema as Gemini's response schema.
        completion = self._complete(messages, LLMAnswer, timeout_s)
        if completion.input_tokens is None or completion.output_tokens is None:
            # No usage reported: the same 4-characters-per-token estimate as the fake LLM.
            estimated = True
            prompt_chars = sum(len(message["content"]) for message in messages)
            input_tokens = math.ceil(prompt_chars / 4)
            output_tokens = math.ceil(len(completion.text) / 4)
        else:
            estimated = False
            input_tokens, output_tokens = completion.input_tokens, completion.output_tokens
        return LLMResult(
            text=completion.text,
            model=completion.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=estimated,
            latency_ms=completion.latency_ms,
        )


class LiteLLMJSONClient(_ChatModel):
    """`JSONCompletionClient` for the small tasks (the fast model)."""

    def complete_json(self, system: str, user: str, *, timeout_s: float) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        completion = self._complete(messages, {"type": "json_object"}, timeout_s)
        try:
            data = json.loads(strip_code_fence(completion.text))
        except json.JSONDecodeError as exc:
            raise PermanentProviderError("LLM reply is not JSON") from exc
        if not isinstance(data, dict):
            raise PermanentProviderError("LLM reply is not a JSON object")
        return data

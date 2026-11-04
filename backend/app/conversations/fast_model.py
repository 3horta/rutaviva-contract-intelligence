"""One traced call to the fast model, for the small jobs AFTER an answer (title,
suggestions). Same span attributes as `llm.generate`: model, latency and tokens when
the provider reports them. Never prompts or replies."""

import html
import time
from typing import Any

from app.core.tracing import (
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_REQUEST_MODEL,
    LLM_LATENCY_MS,
    set_attributes,
    span,
)
from app.providers.llm.base import JSONCompletionClient


def complete_json_traced(
    client: JSONCompletionClient, span_name: str, system: str, user: str, *, timeout_s: float
) -> dict[str, Any]:
    """`client.complete_json` inside a span `span_name`. Errors propagate (the span is
    marked as failed); callers turn them into "skip this step"."""
    with span(span_name, **{GEN_AI_REQUEST_MODEL: getattr(client, "model_id", None)}) as current:
        started = time.perf_counter()
        try:
            return client.complete_json(system, user, timeout_s=timeout_s)
        finally:
            usage = getattr(client, "last_usage", None) or (None, None)
            set_attributes(
                current,
                **{
                    LLM_LATENCY_MS: round((time.perf_counter() - started) * 1000),
                    GEN_AI_INPUT_TOKENS: usage[0],
                    GEN_AI_OUTPUT_TOKENS: usage[1],
                },
            )


def as_data(text: str) -> str:
    """Text placed inside a prompt tag: `<` and `>` escaped, so it cannot close the tag
    it is in or open another one."""
    return html.escape(text, quote=False)


def one_line(text: str) -> str:
    return " ".join(text.split())

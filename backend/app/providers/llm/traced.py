"""One traced call to a JSON chat model: the small jobs around an answer (resolving a
follow-up, judging relevance, a conversation title, suggested follow-ups).

Each call is a GenAI `chat {model}` CLIENT span (ADR-018) with the prompt template's
name, the requested output type (`json`), latency and the tokens the provider reports.
Never the prompt or the reply."""

import time
from typing import Any

from app.core.tracing import (
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_OUTPUT_TYPE,
    GEN_AI_PROMPT_NAME,
    LLM_LATENCY_MS,
    OP_CHAT,
    model_span,
    set_attributes,
)
from app.providers.llm.base import JSONCompletionClient

# `gen_ai.prompt.name` of each prompt template.
PROMPT_ANSWER = "answer"
PROMPT_RELEVANCE_JUDGE = "relevance_judge"
PROMPT_RESOLVE_FOLLOW_UP = "resolve_follow_up"
PROMPT_CONVERSATION_TITLE = "conversation_title"
PROMPT_FOLLOW_UP_SUGGESTIONS = "follow_up_suggestions"


def complete_json_traced(
    client: JSONCompletionClient, prompt_name: str, system: str, user: str, *, timeout_s: float
) -> dict[str, Any]:
    """`client.complete_json` inside its `chat` span. Errors propagate (the span gets
    `error.type`); callers turn them into "skip this step"."""
    model_id = getattr(client, "model_id", None) or "unknown"
    with model_span(
        OP_CHAT, model_id, **{GEN_AI_PROMPT_NAME: prompt_name, GEN_AI_OUTPUT_TYPE: "json"}
    ) as current:
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

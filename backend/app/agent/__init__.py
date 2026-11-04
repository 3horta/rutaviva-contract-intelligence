"""Agent mode (ADR-021): answering as a LangGraph agent with function calling.

`answer(...)` is the single entry point for every caller (chat, `POST /query`, MCP):
- `QUERY_MODE=auto` (default): the agent graph. With a real LLM (`LLM_PROVIDER=litellm`)
  Gemini decides which tools to call; keyless, a deterministic policy runs the same
  steps as the workflow through the same graph, so the answers are the workflow's.
- `QUERY_MODE=workflow`: the fixed pipeline (`generation.service.answer_with_usage`).

Privacy: LangChain's own tracing service (LangSmith) is switched off here, before any
LangChain module is imported, whatever the environment says. Traces go only through
OpenTelemetry, with ids and counts, never content (ADR-018).
"""

import os

# Forced, not defaulted: an inherited LANGSMITH_TRACING=true must not send prompts and
# contract text to a third-party service.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import uuid  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation.service import AnswerOutcome, PhaseCallback, answer_with_usage  # noqa: E402
from app.providers.embeddings.base import EmbeddingsProvider  # noqa: E402
from app.providers.llm.base import LLMProvider  # noqa: E402
from app.retrieval.judge import RelevanceJudge  # noqa: E402


def answer(
    question: str,
    *,
    tenant_id: uuid.UUID,
    session: Session,
    embeddings: EmbeddingsProvider,
    llm: LLMProvider,
    settings: Settings,
    on_phase: PhaseCallback | None = None,
    judge: RelevanceJudge | None = None,
    conversation_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    """Answer in the configured mode. `judge` only applies to the workflow: in agent
    mode the model itself decides which passages are relevant."""
    if settings.query_mode == "workflow":
        return answer_with_usage(
            question,
            tenant_id=tenant_id,
            session=session,
            embeddings=embeddings,
            llm=llm,
            settings=settings,
            on_phase=on_phase,
            judge=judge,
            conversation_id=conversation_id,
        )
    from app.agent.graph import run_agent

    return run_agent(
        question,
        tenant_id=tenant_id,
        session=session,
        embeddings=embeddings,
        llm=llm,
        settings=settings,
        on_phase=on_phase,
        conversation_id=conversation_id,
    )

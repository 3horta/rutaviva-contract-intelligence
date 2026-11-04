"""The agent's graph state and its per-run context.

State (checkpointable data, what the graph accumulates):
- `messages`: the conversation with the model (question, tool calls, tool results).
- `evidence`: every chunk a tool returned, in the order it was first seen. It is the
  ledger the answer is validated against: a citation must quote a passage the agent
  actually read in this run.
- the outcome so far: the validated answer, attempts, tokens, why it stopped.

Context (`AgentContext`): what the tools and nodes need but the model must never see or
choose: the tenant, the database session, the providers and the settings. It is passed
at invocation (`graph.invoke(..., context=...)`), never as a tool argument and never in
the state, so a model cannot ask for another tenant.
"""

import operator
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.providers.embeddings.base import EmbeddingsProvider
from app.providers.llm.base import LLMProvider
from app.retrieval.hybrid import RetrievedChunk
from app.schemas.query import LLMAnswer

# Why the run ended without a validated answer (or `answered`).
StopReason = Literal["answered", "validation_failed", "content_filtered", "budget"]


def merge_evidence(
    current: list[RetrievedChunk], new: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Reducer: append chunks not seen yet (by id), keeping first-seen order."""
    seen = {chunk.chunk_id for chunk in current}
    merged = list(current)
    for chunk in new:
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            merged.append(chunk)
    return merged


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    evidence: Annotated[list[RetrievedChunk], merge_evidence]
    # Model turns taken (each `decide` call), against `AGENT_MAX_STEPS`.
    steps: Annotated[int, operator.add]
    # Answers submitted (valid or not), against `1 + LLM_MAX_RETRIES`.
    attempts: Annotated[int, operator.add]
    tokens_in: Annotated[int, operator.add]
    tokens_out: Annotated[int, operator.add]
    answer: LLMAnswer | None
    stop_reason: StopReason | None


@dataclass(frozen=True)
class AgentContext:
    question: str
    tenant_id: uuid.UUID
    session: Session
    embeddings: EmbeddingsProvider
    llm: LLMProvider
    settings: Settings
    notify: Callable[[str], None]
    conversation_id: uuid.UUID | None = None


def initial_state(question: str) -> AgentState:
    from langchain_core.messages import HumanMessage

    return AgentState(
        messages=[HumanMessage(content=question)],
        evidence=[],
        steps=0,
        attempts=0,
        tokens_in=0,
        tokens_out=0,
        answer=None,
        stop_reason=None,
    )

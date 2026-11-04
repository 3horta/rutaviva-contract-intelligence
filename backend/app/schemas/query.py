"""API and LLM models for questions and answers."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Confidence = Literal["low", "medium", "high"]
AnswerStatusOut = Literal["answered", "insufficient_evidence"]

# Exact text returned when there is not enough evidence (the status carries the meaning;
# the text is for people).
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "There is not enough information in your documents to answer this question."
)

# Codes that can appear in `QueryResponse.warnings` (machine codes; the UI shows the
# label). See ADR-013.
WARNING_PROMPT_INJECTION = "prompt_injection"
WARNING_SOURCES_DISAGREE = "sources_disagree"
WARNING_LABELS: dict[str, str] = {
    WARNING_PROMPT_INJECTION: (
        "A passage that looked like instructions to the assistant was ignored."
    ),
    WARNING_SOURCES_DISAGREE: (
        "The cited documents give different values; check which one applies."
    ),
}


# --- What the LLM must return (validated; invalid output is retried) --------------------


class LLMCitation(BaseModel):
    chunk_id: uuid.UUID
    # Verbatim excerpt of the chunk that supports the answer; checked against the chunk.
    quote: str = Field(min_length=1)


class LLMAnswer(BaseModel):
    answer: str
    citations: list[LLMCitation]
    confidence: Confidence
    insufficient_evidence: bool


# --- API -------------------------------------------------------------------------------


class QueryRequest(BaseModel):
    # Stripped before the length check: a whitespace-only question is a 422, not a search.
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    conversation_id: uuid.UUID | None = None
    # Ask the last question of the conversation again and REPLACE its last turn (the
    # previous answer is deleted when the new one is saved). Requires `conversation_id`
    # and the same question text as the last turn; otherwise 422.
    retry: bool = False


class Citation(BaseModel):
    """A citation enriched by the server from the stored chunk (the LLM only gives
    chunk_id and quote)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    page: int
    quote: str


FoundBy = Literal["vector", "full_text", "both"]
# `all_flagged`: every retrieved chunk was flagged as prompt injection and excluded.
InsufficientReason = Literal["no_results", "all_flagged", "llm_declared", "validation_failed"]


class RetrievedChunkOut(BaseModel):
    """A chunk the answer was generated from. Ids, page and score only: never content."""

    document_id: uuid.UUID
    document_filename: str
    page: int
    chunk_id: uuid.UUID
    score: float  # fused RRF score (only comparable within the same answer)
    found_by: FoundBy
    # Flagged as prompt injection at indexing: listed here, but not given to the LLM.
    flagged: bool = False


class AnswerDetails(BaseModel):
    """How an answer was produced, for the "how was this answered" panel."""

    retrieved: list[RetrievedChunkOut]
    # Of the retrieved chunks, how many each search branch found (a chunk found by both
    # counts in both).
    vector_hits: int
    full_text_hits: int
    # LLM calls made (0 when retrieval found nothing and the LLM was not called).
    attempts: int
    insufficient_reason: InsufficientReason | None
    latency_ms: int
    llm_model: str
    trace_id: str | None
    # The question actually searched when a follow-up was rewritten with the conversation
    # context; null when the question was used as typed.
    standalone_question: str | None = None
    uses_context: bool = False


class QueryResponse(BaseModel):
    conversation_id: uuid.UUID | None
    answer: str
    status: AnswerStatusOut
    confidence: Confidence
    citations: list[Citation]
    # Machine codes (`WARNING_LABELS` has the text to show), e.g. "prompt_injection".
    warnings: list[str]
    # The stored assistant message; null only when the answer was not persisted.
    message_id: uuid.UUID | None = None
    details: AnswerDetails | None = None

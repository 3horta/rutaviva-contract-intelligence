"""API and LLM models for questions and answers."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]
AnswerStatusOut = Literal["answered", "insufficient_evidence"]

# Exact text returned when there is not enough evidence (the status carries the meaning;
# the text is for people).
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "There is not enough information in your documents to answer this question."
)


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
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class Citation(BaseModel):
    """A citation enriched by the server from the stored chunk (the LLM only gives
    chunk_id and quote)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    page: int
    quote: str


class QueryResponse(BaseModel):
    conversation_id: uuid.UUID | None
    answer: str
    status: AnswerStatusOut
    confidence: Confidence
    citations: list[Citation]
    warnings: list[str]

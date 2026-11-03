"""Validate the raw LLM output in three layers before anything reaches the user.

1. Syntax: the text parses as JSON (a surrounding ```json fence is tolerated).
2. Schema: the JSON validates as `LLMAnswer`.
3. Grounding: every citation points to a chunk that was actually given to the model,
   and its quote appears in that chunk (comparison ignores case and whitespace runs).
   An answer that claims to have evidence (`insufficient_evidence=false`) must have
   text and at least one citation.

The result is either a `ValidatedAnswer` or a `ValidationFailure`. The failure's
`reason` is a short fixed code, safe for spans and logs; `detail` explains the problem
and is only fed back to the model on the retry (it may mention chunk ids, never content).
"""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.providers.llm.base import ContextChunk
from app.schemas.query import LLMAnswer

FailureReason = Literal[
    "not_json",  # layer 1
    "schema",  # layer 2
    "unknown_chunk",  # layer 3: cites a chunk that was not in the context
    "quote_not_found",  # layer 3: the quote is not in the cited chunk
    "no_citations",  # layer 3: claims evidence but cites nothing (or has no text)
]


@dataclass(frozen=True)
class ValidatedAnswer:
    answer: LLMAnswer


@dataclass(frozen=True)
class ValidationFailure:
    reason: FailureReason
    detail: str


def normalise(text: str) -> str:
    """Lowercase and collapse whitespace: a quote is still "verbatim" if the model
    changed a line break into a space or capitalised the first word."""
    return " ".join(text.split()).casefold()


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def validate_answer(
    raw: str, chunks: Sequence[ContextChunk]
) -> ValidatedAnswer | ValidationFailure:
    # Layer 1: JSON syntax.
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return ValidationFailure("not_json", "The reply was not a valid JSON object.")

    # Layer 2: shape and types.
    try:
        answer = LLMAnswer.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) or "root" for err in exc.errors()})
        return ValidationFailure(
            "schema", f"The JSON does not match the schema; check: {', '.join(fields)}."
        )

    # Layer 3: grounding in the chunks that were actually provided.
    texts: dict[uuid.UUID, str] = {chunk.chunk_id: normalise(chunk.text) for chunk in chunks}
    for number, citation in enumerate(answer.citations, start=1):
        chunk_text = texts.get(citation.chunk_id)
        if chunk_text is None:
            return ValidationFailure(
                "unknown_chunk",
                f"Citation {number} uses chunk id {citation.chunk_id}, which is not one of "
                "the provided documents. Only cite the given ids.",
            )
        if normalise(citation.quote) not in chunk_text:
            return ValidationFailure(
                "quote_not_found",
                f"The quote of citation {number} does not appear in chunk "
                f"{citation.chunk_id}. Copy quotes verbatim from the document.",
            )

    if not answer.insufficient_evidence and (not answer.citations or not answer.answer.strip()):
        return ValidationFailure(
            "no_citations",
            "An answer must have text and cite at least one document; if the documents "
            "do not answer the question, set insufficient_evidence to true.",
        )

    return ValidatedAnswer(answer)

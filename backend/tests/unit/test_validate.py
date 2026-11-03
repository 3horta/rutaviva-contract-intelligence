import json
import uuid

import pytest

from app.generation.validate import ValidatedAnswer, ValidationFailure, validate_answer
from app.providers.llm.base import ContextChunk

CHUNK = ContextChunk(
    chunk_id=uuid.uuid4(),
    document_id=uuid.uuid4(),
    filename="tire_supply_agreement.pdf",
    page=2,
    text="Late delivery.  Supplier shall pay a penalty of 1.5%\nper business day, capped at 15%.",
)


def _raw(**overrides) -> str:
    data = {
        "answer": "A penalty of 1.5% per business day, capped at 15%.",
        "citations": [{"chunk_id": str(CHUNK.chunk_id), "quote": "a penalty of 1.5% per"}],
        "confidence": "high",
        "insufficient_evidence": False,
    }
    data.update(overrides)
    return json.dumps(data)


def _reason(raw: str) -> str:
    outcome = validate_answer(raw, [CHUNK])
    assert isinstance(outcome, ValidationFailure)
    assert outcome.detail  # always something to feed back to the model
    return outcome.reason


def test_valid_answer_passes() -> None:
    outcome = validate_answer(_raw(), [CHUNK])
    assert isinstance(outcome, ValidatedAnswer)
    assert outcome.answer.citations[0].chunk_id == CHUNK.chunk_id


def test_json_inside_a_code_fence_is_accepted() -> None:
    assert isinstance(validate_answer(f"```json\n{_raw()}\n```", [CHUNK]), ValidatedAnswer)


# --- layer 1: JSON ---------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "Sure! The penalty is 1.5%.", "{'answer': 1}", "{"])
def test_non_json_is_rejected(raw: str) -> None:
    assert _reason(raw) == "not_json"


# --- layer 2: schema -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(["not", "an", "object"]),
        _raw(confidence="certain"),
        _raw(citations=[{"chunk_id": "not-a-uuid", "quote": "x"}]),
        _raw(citations=[{"chunk_id": str(CHUNK.chunk_id), "quote": ""}]),
        json.dumps({"answer": "missing fields"}),
    ],
)
def test_json_that_does_not_match_the_schema_is_rejected(raw: str) -> None:
    assert _reason(raw) == "schema"


# --- layer 3: grounding ----------------------------------------------------------------


def test_invented_chunk_id_is_rejected() -> None:
    invented = [{"chunk_id": str(uuid.uuid4()), "quote": "a penalty of 1.5% per"}]
    assert _reason(_raw(citations=invented)) == "unknown_chunk"


def test_quote_not_in_the_chunk_is_rejected() -> None:
    made_up = [{"chunk_id": str(CHUNK.chunk_id), "quote": "a penalty of 3% per day"}]
    assert _reason(_raw(citations=made_up)) == "quote_not_found"


def test_quote_matching_ignores_case_and_whitespace() -> None:
    # The chunk has a line break and a double space; the quote has single spaces.
    quote = "LATE DELIVERY. Supplier shall pay a penalty of 1.5% per business day"
    raw = _raw(citations=[{"chunk_id": str(CHUNK.chunk_id), "quote": quote}])
    assert isinstance(validate_answer(raw, [CHUNK]), ValidatedAnswer)


def test_answer_with_evidence_but_no_citations_is_rejected() -> None:
    assert _reason(_raw(citations=[])) == "no_citations"


def test_answer_with_evidence_but_empty_text_is_rejected() -> None:
    assert _reason(_raw(answer="  ")) == "no_citations"


def test_insufficient_evidence_without_citations_is_valid() -> None:
    raw = _raw(answer="", citations=[], confidence="low", insufficient_evidence=True)
    outcome = validate_answer(raw, [CHUNK])
    assert isinstance(outcome, ValidatedAnswer)
    assert outcome.answer.insufficient_evidence

"""Contract of the fake LLM: deterministic, extractive, and output the validator accepts."""

import json
import uuid

import pytest

from app.core.errors import ProviderTimeoutError
from app.generation.validate import ValidatedAnswer, validate_answer
from app.providers.llm.base import ContextChunk, PromptInput
from app.providers.llm.fake import FakeLLM
from app.schemas.query import LLMAnswer

CHUNKS = [
    ContextChunk(
        chunk_id=uuid.UUID("00000000-0000-0000-0000-00000000000a"),
        document_id=uuid.uuid4(),
        filename="tire_supply_agreement_frontera.pdf",
        page=2,
        text=(
            "5. Delivery. Frontera shall deliver each purchase order within seventy-two "
            "(72) business hours. For each business day of late delivery, Frontera shall "
            "pay a penalty of 1.5% of the order value, capped at 15%."
        ),
    ),
    ContextChunk(
        chunk_id=uuid.UUID("00000000-0000-0000-0000-00000000000b"),
        document_id=uuid.uuid4(),
        filename="fleet_insurance_policy_centinela.pdf",
        page=1,
        text="Motor truck cargo coverage has a limit of $250,000 and a deductible of $5,000.",
    ),
]
QUESTION = "What penalty applies to late tire delivery?"


def _prompt(question: str = QUESTION, attempt: int = 1) -> PromptInput:
    return PromptInput(question=question, chunks=CHUNKS, attempt=attempt)


def _answer(question: str = QUESTION) -> LLMAnswer:
    return LLMAnswer.model_validate_json(
        FakeLLM("ok").generate(_prompt(question), timeout_s=1).text
    )


def test_same_input_gives_identical_output() -> None:
    outputs = {FakeLLM("ok").generate(_prompt(), timeout_s=1).text for _ in range(5)}
    assert len(outputs) == 1


def test_answer_cites_the_matching_sentence_verbatim() -> None:
    answer = _answer()
    assert not answer.insufficient_evidence
    assert answer.citations
    by_id = {chunk.chunk_id: chunk for chunk in CHUNKS}
    for citation in answer.citations:
        assert citation.quote in by_id[citation.chunk_id].text  # exact substring
    assert "1.5%" in answer.answer
    assert answer.citations[0].chunk_id == CHUNKS[0].chunk_id


def test_output_passes_validation() -> None:
    result = FakeLLM("ok").generate(_prompt(), timeout_s=1)
    assert isinstance(validate_answer(result.text, CHUNKS), ValidatedAnswer)


def test_no_matching_words_means_insufficient_evidence() -> None:
    answer = _answer("Does the policy cover electric scooters for employees?")
    assert answer.insufficient_evidence
    assert answer.citations == []


def test_usage_is_estimated() -> None:
    result = FakeLLM("ok").generate(_prompt(), timeout_s=1)
    assert result.tokens_estimated is True
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.latency_ms >= 0
    assert result.model == "fake-extractive-v1"


def test_mode_invalid_never_returns_json() -> None:
    llm = FakeLLM("invalid")
    for attempt in (1, 2, 3):
        with pytest.raises(json.JSONDecodeError):
            json.loads(llm.generate(_prompt(attempt=attempt), timeout_s=1).text)


def test_mode_invalid_once_fails_only_the_first_attempt() -> None:
    llm = FakeLLM("invalid_once")
    with pytest.raises(json.JSONDecodeError):
        json.loads(llm.generate(_prompt(attempt=1), timeout_s=1).text)
    LLMAnswer.model_validate_json(llm.generate(_prompt(attempt=2), timeout_s=1).text)


def test_mode_timeout_raises_provider_timeout() -> None:
    with pytest.raises(ProviderTimeoutError):
        FakeLLM("timeout").generate(_prompt(), timeout_s=1)


def test_mode_empty_returns_empty_text() -> None:
    assert FakeLLM("empty").generate(_prompt(), timeout_s=1).text == ""

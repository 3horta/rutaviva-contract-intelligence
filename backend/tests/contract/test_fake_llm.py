"""Contract of the fake LLM: deterministic, extractive, and output the validator accepts.

Most tests use the simulated embedder, as every automated test does. `KeywordEmbeddings`
stands in for a real semantic model: it maps texts to concepts, not words, which shows
that the ranking comes from the embedder and not from shared words.
"""

import json
import uuid
from collections.abc import Sequence

import pytest

from app.core.errors import ProviderTimeoutError, TransientProviderError
from app.generation.validate import ValidatedAnswer, validate_answer
from app.providers.embeddings.fake import FakeEmbeddings
from app.providers.llm.base import ContextChunk, PromptInput
from app.providers.llm.fake import (
    DEFAULT_THRESHOLDS,
    FakeLLM,
    SimilarityThresholds,
    split_sentences,
    thresholds_for,
)
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


def _llm(mode: str = "ok") -> FakeLLM:
    return FakeLLM(FakeEmbeddings(768), mode)  # type: ignore[arg-type]


def _prompt(question: str = QUESTION, attempt: int = 1) -> PromptInput:
    return PromptInput(question=question, chunks=CHUNKS, attempt=attempt)


def _answer(question: str = QUESTION, llm: FakeLLM | None = None) -> LLMAnswer:
    llm = llm or _llm()
    return LLMAnswer.model_validate_json(llm.generate(_prompt(question), timeout_s=1).text)


class KeywordEmbeddings:
    """A toy "semantic" model: one axis per concept, recognised from any of its synonyms."""

    model_id = "toy-semantic@3"
    dim = 3
    tokens_estimated = True
    CONCEPTS = (
        {"late", "penalty", "deadline", "arrives", "after"},
        {"coverage", "insurance", "insured", "deductible"},
        {"tire", "tires", "truck"},
    )

    def _embed(self, text: str) -> list[float]:
        words = set(text.lower().replace("?", " ").replace(".", " ").split())
        vector = [float(len(words & concept)) for concept in self.CONCEPTS]
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def count_tokens(self, texts: Sequence[str]) -> int:
        return 0


def test_same_input_gives_identical_output() -> None:
    outputs = {_llm().generate(_prompt(), timeout_s=1).text for _ in range(5)}
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


def test_headings_are_never_the_answer() -> None:
    # "Delivery." is the fragment closest to the question by the embedder, but alone it
    # answers nothing.
    answer = _answer()
    assert all(c.quote not in {"5.", "Delivery."} for c in answer.citations)


def test_headings_are_joined_to_the_next_sentence_verbatim() -> None:
    text = (
        "Incident Reporting Protocol RV-IRP-7. Supplier shall report any accident "
        "to the  Network Operations Center.\n7.2 Late Delivery.\nPayment is due in 30 days. "
        "8. Notices."
    )
    sentences = split_sentences(text)
    assert sentences == [
        "Incident Reporting Protocol RV-IRP-7. Supplier shall report any accident "
        "to the  Network Operations Center.",
        # Short fragments keep merging forward; what is left at the end is kept because
        # it is long enough to be a real sentence.
        "7.2 Late Delivery.\nPayment is due in 30 days. 8. Notices.",
    ]
    assert all(sentence in text for sentence in sentences)
    # A bare heading at the end of a chunk (cut off by the chunk boundary) is dropped.
    assert split_sentences("Supplier shall report any accident within two hours. 8. Notices.") == [
        "Supplier shall report any accident within two hours."
    ]


def test_output_passes_validation() -> None:
    result = _llm().generate(_prompt(), timeout_s=1)
    assert isinstance(validate_answer(result.text, CHUNKS), ValidatedAnswer)


def test_unrelated_question_means_insufficient_evidence() -> None:
    answer = _answer("Does the policy cover electric scooters for employees?")
    assert answer.insufficient_evidence
    assert answer.citations == []


def test_ranking_comes_from_the_embedder_not_from_shared_words() -> None:
    # No content word in common with the penalty sentence; the toy model knows that
    # "arrives after the deadline" means late.
    llm = FakeLLM(
        KeywordEmbeddings(),  # type: ignore[arg-type]
        thresholds=SimilarityThresholds(minimum=0.5, strong=0.9),
    )
    answer = _answer("What happens if it arrives after the deadline?", llm)
    assert not answer.insufficient_evidence
    assert "1.5%" in answer.citations[0].quote


def test_confidence_follows_the_best_similarity() -> None:
    high = FakeLLM(FakeEmbeddings(768), thresholds=SimilarityThresholds(0.1, 0.2))
    medium = FakeLLM(FakeEmbeddings(768), thresholds=SimilarityThresholds(0.1, 0.99))
    assert _answer(llm=high).confidence == "high"
    assert _answer(llm=medium).confidence == "medium"


def test_thresholds_depend_on_the_embedding_model() -> None:
    assert thresholds_for("fake-hash-v1@768").minimum < DEFAULT_THRESHOLDS.minimum
    assert thresholds_for("bge-base-en-v1.5@768") == DEFAULT_THRESHOLDS


def test_embedding_errors_propagate() -> None:
    class Down(FakeEmbeddings):
        def embed_query(self, text: str) -> list[float]:
            raise TransientProviderError("embedder unavailable")

    with pytest.raises(TransientProviderError):
        FakeLLM(Down(768)).generate(_prompt(), timeout_s=1)


def test_usage_is_estimated() -> None:
    result = _llm().generate(_prompt(), timeout_s=1)
    assert result.tokens_estimated is True
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.latency_ms >= 0
    assert result.model == "fake-extractive-v2"


def test_mode_invalid_never_returns_json() -> None:
    llm = _llm("invalid")
    for attempt in (1, 2, 3):
        with pytest.raises(json.JSONDecodeError):
            json.loads(llm.generate(_prompt(attempt=attempt), timeout_s=1).text)


def test_mode_invalid_once_fails_only_the_first_attempt() -> None:
    llm = _llm("invalid_once")
    with pytest.raises(json.JSONDecodeError):
        json.loads(llm.generate(_prompt(attempt=1), timeout_s=1).text)
    LLMAnswer.model_validate_json(llm.generate(_prompt(attempt=2), timeout_s=1).text)


def test_mode_timeout_raises_provider_timeout() -> None:
    with pytest.raises(ProviderTimeoutError):
        _llm("timeout").generate(_prompt(), timeout_s=1)


def test_mode_empty_returns_empty_text() -> None:
    assert _llm("empty").generate(_prompt(), timeout_s=1).text == ""

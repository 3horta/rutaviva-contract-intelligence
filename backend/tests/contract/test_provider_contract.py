"""The provider contract, run against every implementation: the keyless ones always, the
real ones (Gemini through LiteLLM) only with a key.

What any implementation must do, whatever model is behind it:
- embeddings: the schema's dimension, unit-length vectors, related text closer than
  unrelated text, and a token count;
- LLM: output that the validator accepts (JSON, citations quoted verbatim from the given
  chunks), "insufficient evidence" for a question the chunks do not answer, and usage.

The real providers are marked `live` and skipped unless GEMINI_API_KEY is in the
environment (never read from .env here). They call the real API and cost a little:

    GEMINI_API_KEY=... uv run pytest -m live tests/contract
    # optional: LIVE_LLM_MODEL=gemini/<model> (default gemini/gemini-3.5-flash)
"""

import math
import os
import uuid

import pytest

from app.conversations.resolver import LLMQuestionResolver, Turn
from app.generation.validate import ValidatedAnswer, strip_code_fence, validate_answer
from app.providers.embeddings.fake import FakeEmbeddings
from app.providers.llm.base import ContextChunk, PromptInput
from app.providers.llm.fake import FakeLLM
from app.schemas.query import LLMAnswer

# Read at import (collection), before the autouse fixture clears the environment.
LIVE_KEY = os.getenv("GEMINI_API_KEY", "")
LIVE_LLM_MODEL = os.getenv("LIVE_LLM_MODEL", "gemini/gemini-3.5-flash")
LIVE_EMBEDDING_MODEL = "gemini-embedding-001"
DIM = 768

live = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE_KEY, reason="GEMINI_API_KEY not set: real providers skipped"),
]


def _gemini_embeddings():
    from app.providers.embeddings.gemini import GeminiEmbeddings

    return GeminiEmbeddings(LIVE_EMBEDDING_MODEL, LIVE_KEY, DIM)


def _litellm_llm():
    from app.providers.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(LIVE_LLM_MODEL, LIVE_KEY)


@pytest.fixture(
    params=[
        pytest.param(lambda: FakeEmbeddings(DIM), id="fake"),
        pytest.param(_gemini_embeddings, id="gemini", marks=live),
    ]
)
def embeddings(request):
    return request.param()


@pytest.fixture(
    params=[
        pytest.param(lambda: FakeLLM(FakeEmbeddings(DIM)), id="fake"),
        pytest.param(_litellm_llm, id="litellm", marks=live),
    ]
)
def llm(request):
    return request.param()


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


# --- Embeddings ---------------------------------------------------------------------------


def test_embeddings_have_the_schema_dimension_and_unit_norm(embeddings) -> None:
    vectors = [*embeddings.embed_documents(["Late delivery penalty."]), embeddings.embed_query("x")]
    for vector in vectors:
        assert len(vector) == DIM
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)


def test_related_text_is_closer_than_unrelated_text(embeddings) -> None:
    query = embeddings.embed_query("penalty for late tire deliveries")
    related, unrelated = embeddings.embed_documents(
        [
            "Supplier pays a penalty when a tire delivery is late.",
            "The insurance policy covers cargo up to $250,000.",
        ]
    )
    assert _cosine(query, related) > _cosine(query, unrelated)


def test_embeddings_count_tokens(embeddings) -> None:
    assert embeddings.count_tokens(["late delivery penalty"]) > 0
    assert embeddings.model_id.endswith(f"@{DIM}")


# --- LLM ----------------------------------------------------------------------------------

CHUNKS = [
    ContextChunk(
        chunk_id=uuid.uuid4(),
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
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="fleet_insurance_policy_centinela.pdf",
        page=1,
        text="Motor truck cargo coverage has a limit of $250,000 and a deductible of $5,000.",
    ),
]


def _ask(llm, question: str):
    return llm.generate(PromptInput(question=question, chunks=CHUNKS), timeout_s=30)


def test_llm_answer_passes_validation(llm) -> None:
    result = _ask(llm, "What penalty applies to late tire delivery?")
    outcome = validate_answer(result.text, CHUNKS)
    assert isinstance(outcome, ValidatedAnswer), outcome
    assert not outcome.answer.insufficient_evidence
    assert outcome.answer.citations[0].chunk_id == CHUNKS[0].chunk_id


def test_llm_declares_insufficient_evidence_for_an_unanswerable_question(llm) -> None:
    result = _ask(llm, "Does the policy cover electric scooters for employees?")
    answer = LLMAnswer.model_validate_json(strip_code_fence(result.text))
    assert answer.insufficient_evidence
    assert answer.citations == []


def test_llm_reports_usage(llm) -> None:
    result = _ask(llm, "What penalty applies to late tire delivery?")
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.latency_ms >= 0
    assert result.model


# --- JSON client (real only: the keyless mode has deterministic stand-ins) ----------------


@pytest.mark.live
@pytest.mark.skipif(not LIVE_KEY, reason="GEMINI_API_KEY not set: real providers skipped")
def test_live_resolver_rewrites_a_follow_up() -> None:
    from app.providers.llm.litellm_provider import LiteLLMJSONClient

    resolver = LLMQuestionResolver(LiteLLMJSONClient(LIVE_LLM_MODEL, LIVE_KEY))
    turns = [Turn("What is the penalty for late tire delivery?", "1.5% per business day.")]
    resolved = resolver.resolve("and what is the cap?", turns)
    assert resolved.uses_context
    assert "cap" in resolved.standalone.lower()

"""Every path of `answer_question`, with retrieval stubbed and a scripted LLM double."""

import json
import uuid

import pytest

from app.core.config import Settings
from app.core.errors import (
    GatewayTimeout,
    PermanentProviderError,
    ProviderTimeoutError,
    ServiceUnavailable,
    TransientProviderError,
)
from app.generation import service
from app.providers.embeddings.fake import FakeEmbeddings
from app.providers.llm.base import LLMResult, PromptInput
from app.retrieval.hybrid import RetrievedChunk
from app.schemas.query import INSUFFICIENT_EVIDENCE_MESSAGE

TENANT = uuid.uuid4()
DOCUMENT = uuid.uuid4()
CHUNK = RetrievedChunk(
    chunk_id=uuid.uuid4(),
    document_id=DOCUMENT,
    filename="tire_supply_agreement_frontera.pdf",
    page=2,
    content="Supplier shall pay a penalty of 1.5% per business day, capped at 15%.",
    flagged_injection=False,
    score=0.016,
    vec_rank=1,
    fts_rank=1,
)
QUOTE = "a penalty of 1.5% per business day"


def _valid(confidence: str = "high", chunk_id: uuid.UUID = CHUNK.chunk_id, quote: str = QUOTE):
    return json.dumps(
        {
            "answer": "1.5% per business day, capped at 15%.",
            "citations": [{"chunk_id": str(chunk_id), "quote": quote}],
            "confidence": confidence,
            "insufficient_evidence": False,
        }
    )


class ScriptedLLM:
    """Returns (or raises) the scripted items in order and records every prompt."""

    model_id = "scripted"

    def __init__(self, *script: str | Exception) -> None:
        self.script = list(script)
        self.prompts: list[PromptInput] = []

    def generate(self, prompt: PromptInput, *, timeout_s: float) -> LLMResult:
        self.prompts.append(prompt)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(
            text=item,
            model=self.model_id,
            input_tokens=10,
            output_tokens=5,
            tokens_estimated=True,
            latency_ms=1,
        )


@pytest.fixture
def retrieved(monkeypatch: pytest.MonkeyPatch) -> list[RetrievedChunk]:
    """What the stubbed `search` returns; tests may change it in place."""
    chunks = [CHUNK]
    monkeypatch.setattr(service, "search", lambda *args, **kwargs: list(chunks))
    return chunks


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the waits instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(service, "_sleep", waits.append)
    return waits


def _ask(llm: ScriptedLLM, max_retries: int = 2):
    settings = Settings(_env_file=None, llm_max_retries=max_retries)
    return service.answer_question(
        "What is the penalty for late tire delivery?",
        tenant_id=TENANT,
        session=None,  # unused: `search` is stubbed
        embeddings=FakeEmbeddings(768),
        llm=llm,
        settings=settings,
    )


def _assert_insufficient(response) -> None:
    assert response.status == "insufficient_evidence"
    assert response.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert response.confidence == "low"
    assert response.citations == []


def test_success_returns_enriched_citations(retrieved) -> None:
    response = _ask(ScriptedLLM(_valid()))
    assert response.status == "answered"
    assert response.conversation_id is None
    assert response.warnings == []
    [citation] = response.citations
    assert citation.chunk_id == CHUNK.chunk_id
    assert citation.document_id == DOCUMENT
    assert citation.document_filename == "tire_supply_agreement_frontera.pdf"
    assert citation.page == 2
    assert citation.quote == QUOTE


def test_invalid_once_then_success_retries_with_feedback(retrieved) -> None:
    llm = ScriptedLLM("not json", _valid())
    response = _ask(llm)
    assert response.status == "answered"
    assert [p.attempt for p in llm.prompts] == [1, 2]
    assert llm.prompts[0].feedback is None
    assert llm.prompts[1].feedback == "The reply was not a valid JSON object."


def test_invalid_every_time_is_insufficient(retrieved) -> None:
    llm = ScriptedLLM("no", "still no", "nope")
    _assert_insufficient(_ask(llm, max_retries=2))
    assert len(llm.prompts) == 3  # 1 + llm_max_retries


def test_invented_chunk_id_is_never_shown(retrieved) -> None:
    llm = ScriptedLLM(_valid(chunk_id=uuid.uuid4()), _valid(chunk_id=uuid.uuid4()))
    _assert_insufficient(_ask(llm, max_retries=1))
    assert "not one of the provided documents" in llm.prompts[1].feedback


def test_quote_not_in_chunk_is_rejected_then_corrected(retrieved) -> None:
    llm = ScriptedLLM(_valid(quote="a penalty of 3% per day"), _valid())
    response = _ask(llm)
    assert response.status == "answered"
    assert "does not appear in chunk" in llm.prompts[1].feedback


def test_llm_declaring_insufficient_evidence(retrieved) -> None:
    declared = json.dumps(
        {"answer": "", "citations": [], "confidence": "low", "insufficient_evidence": True}
    )
    _assert_insufficient(_ask(ScriptedLLM(declared)))


def test_no_retrieval_results_does_not_call_the_llm(retrieved) -> None:
    retrieved.clear()
    llm = ScriptedLLM()  # an empty script: any call would fail
    _assert_insufficient(_ask(llm))
    assert llm.prompts == []


def test_timeout_on_every_attempt_is_gateway_timeout(retrieved) -> None:
    llm = ScriptedLLM(*(ProviderTimeoutError("slow") for _ in range(3)))
    with pytest.raises(GatewayTimeout):
        _ask(llm, max_retries=2)
    assert len(llm.prompts) == 3


def test_timeout_then_success_is_answered(retrieved) -> None:
    response = _ask(ScriptedLLM(ProviderTimeoutError("slow"), _valid()))
    assert response.status == "answered"


def test_transient_error_is_retried_once_after_a_short_wait(retrieved, sleeps) -> None:
    llm = ScriptedLLM(TransientProviderError("503"), _valid())
    assert _ask(llm).status == "answered"
    assert sleeps == [service.TRANSIENT_RETRY_WAIT_S]


def test_transient_error_twice_gives_up_even_with_attempts_left(retrieved, sleeps) -> None:
    # Two attempts remain after the first failure, but a 429/5xx gets only one retry.
    llm = ScriptedLLM(TransientProviderError("429"), TransientProviderError("503"), _valid())
    with pytest.raises(ServiceUnavailable):
        _ask(llm, max_retries=2)
    assert len(llm.prompts) == 2
    assert len(sleeps) == 1


def test_retry_after_is_respected_and_capped(retrieved, sleeps) -> None:
    _ask(ScriptedLLM(TransientProviderError("429", retry_after_s=2.5), _valid()))
    _ask(ScriptedLLM(TransientProviderError("429", retry_after_s=60), _valid()))
    assert sleeps == [2.5, service.MAX_RETRY_AFTER_S]


def test_timeouts_do_not_wait(retrieved, sleeps) -> None:
    _ask(ScriptedLLM(ProviderTimeoutError("slow"), _valid()))
    assert sleeps == []


def test_permanent_error_is_not_retried(retrieved) -> None:
    llm = ScriptedLLM(PermanentProviderError("bad request"), _valid())
    with pytest.raises(ServiceUnavailable):
        _ask(llm)
    assert len(llm.prompts) == 1


def test_confidence_is_capped_when_top_chunk_found_by_one_branch(retrieved) -> None:
    retrieved[0] = RetrievedChunk(**{**CHUNK.__dict__, "fts_rank": None})
    assert _ask(ScriptedLLM(_valid("high"))).confidence == "medium"


def test_confidence_never_exceeds_the_llm_confidence(retrieved) -> None:
    # Top chunk found by both branches (signal "high"), but the LLM said "medium".
    assert _ask(ScriptedLLM(_valid("medium"))).confidence == "medium"
    assert _ask(ScriptedLLM(_valid("high"))).confidence == "high"

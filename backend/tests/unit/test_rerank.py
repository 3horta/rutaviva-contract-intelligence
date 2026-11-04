import logging
import uuid
from typing import Any

import pytest

from app.retrieval.hybrid import RetrievedChunk
from app.retrieval.ranking import select_chunks
from app.retrieval.rerank import CrossEncoderReranker, NoRerank, get_reranker


def _chunk(content: str, score: float, document_id: uuid.UUID | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        filename="contract.pdf",
        page=1,
        content=content,
        flagged_injection=False,
        score=score,
        vec_rank=1,
        fts_rank=None,
    )


class ScriptedScorer:
    """Scores each pair by the given text -> score table; records what it was asked."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> list[float]:
        self.calls.append(pairs)
        return [self.scores[passage] for _, passage in pairs]


class BrokenScorer:
    def __init__(self, result: Any = None) -> None:
        self.result = result

    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> Any:
        if self.result is None:
            raise RuntimeError("model crashed")
        return self.result


NOTICE = "Notices of late delivery shall be sent by email."
PENALTY = "The penalty for late delivery is 1.5% of the order value per business day."
FUEL = "Diesel is priced at the EIA weekly index less $0.12 per gallon."


def test_candidates_are_reordered_by_the_cross_encoder_score() -> None:
    # RRF put the notice clause first (it shares the words); the cross-encoder knows
    # the penalty clause is the one that answers.
    notice, penalty, fuel = _chunk(NOTICE, 0.03), _chunk(PENALTY, 0.02), _chunk(FUEL, 0.01)
    scorer = ScriptedScorer({NOTICE: 1.0, PENALTY: 8.5, FUEL: -9.0})
    reranked = CrossEncoderReranker(scorer, "test").rerank(
        "What is the late delivery penalty?", [notice, penalty, fuel]
    )

    assert [c.content for c in reranked] == [PENALTY, NOTICE, FUEL]
    assert [c.rerank_score for c in reranked] == [8.5, 1.0, -9.0]
    # The RRF score is kept: it is still what the answer details show.
    assert [c.score for c in reranked] == [0.02, 0.03, 0.01]
    question = "What is the late delivery penalty?"
    assert scorer.calls == [[(question, text) for text in (NOTICE, PENALTY, FUEL)]]


def test_equal_scores_keep_the_rrf_order() -> None:
    first, second = _chunk(NOTICE, 0.03), _chunk(PENALTY, 0.02)
    scorer = ScriptedScorer({NOTICE: 2.0, PENALTY: 2.0})
    reranked = CrossEncoderReranker(scorer, "test").rerank("q", [first, second])
    assert [c.chunk_id for c in reranked] == [first.chunk_id, second.chunk_id]


def test_long_chunks_are_clipped_before_scoring() -> None:
    long_text = "x" * 5000
    scorer = ScriptedScorer({"x" * 2000: 1.0, FUEL: 0.0})
    CrossEncoderReranker(scorer, "test").rerank("q", [_chunk(long_text, 0.2), _chunk(FUEL, 0.1)])
    assert len(scorer.calls[0][0][1]) == 2000


def test_fewer_than_two_candidates_do_not_call_the_model() -> None:
    scorer = ScriptedScorer({})
    only = [_chunk(FUEL, 0.1)]
    assert CrossEncoderReranker(scorer, "test").rerank("q", only) == only
    assert CrossEncoderReranker(scorer, "test").rerank("q", []) == []
    assert scorer.calls == []


@pytest.mark.parametrize(
    "scorer",
    [BrokenScorer(), BrokenScorer(result=[1.0])],  # raises; wrong number of scores
    ids=["raises", "wrong-length"],
)
def test_a_failing_model_keeps_the_rrf_order(scorer, caplog) -> None:
    candidates = [_chunk(NOTICE, 0.03), _chunk(PENALTY, 0.02), _chunk(FUEL, 0.01)]
    with caplog.at_level(logging.WARNING):
        result = CrossEncoderReranker(scorer, "test").rerank("q", candidates)
    assert result == candidates
    assert all(c.rerank_score is None for c in result)
    assert "retrieval.rerank_failed" in caplog.text


def test_no_rerank_keeps_the_candidates() -> None:
    candidates = [_chunk(NOTICE, 0.03), _chunk(PENALTY, 0.02)]
    assert NoRerank().rerank("q", candidates) == candidates


def test_reranker_none_needs_no_model(make_settings) -> None:
    assert isinstance(get_reranker(make_settings(RERANKER="none")), NoRerank)


def test_reranker_is_on_by_default(make_settings, monkeypatch) -> None:
    monkeypatch.delenv("RERANKER")  # the test environment turns it off
    settings = make_settings()
    assert settings.reranker == "cross_encoder"
    assert settings.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_selection_follows_the_reranked_order_with_the_same_rules() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    # By RRF the three clauses of A come first; reranked, B's exhibit is the best.
    a = [_chunk(f"clause {i} of the agreement", 0.03 - i / 1000, doc_a) for i in range(3)]
    exhibit = _chunk("the exhibit prevails over the agreement", 0.001, doc_b)
    scorer = ScriptedScorer({**{c.content: 1.0 - i for i, c in enumerate(a)}, exhibit.content: 5.0})
    reranked = CrossEncoderReranker(scorer, "test").rerank("q", [*a, exhibit])

    selected = select_chunks(reranked, k=3, max_per_document=2)
    assert [c.content for c in selected] == [exhibit.content, a[0].content, a[1].content]


def test_warm_up_loads_the_reranker_only_when_asked(make_settings, monkeypatch) -> None:
    import app.providers as providers
    import app.retrieval.rerank as rerank

    loaded = []
    monkeypatch.setattr(rerank, "get_reranker", lambda settings: loaded.append(settings))
    settings = make_settings(RERANKER="cross_encoder")

    providers.warm_up(settings)  # the worker: no reranker
    assert loaded == []
    providers.warm_up(settings, reranker=True)  # the API
    assert loaded == [settings]
    providers.warm_up(make_settings(RERANKER="none"), reranker=True)
    assert len(loaded) == 1

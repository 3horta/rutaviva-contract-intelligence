"""Contract test of the real cross-encoder reranker.

Needs the 'ml' dependency group and the model weights, so it only runs on Linux (Docker
image). Elsewhere it is skipped — unless REQUIRE_ML=1, where a skip would hide that the
real model was never exercised, so it fails instead.
"""

import os
import uuid
from importlib.util import find_spec

import pytest

if find_spec("sentence_transformers") is None:
    if os.getenv("REQUIRE_ML") == "1":
        pytest.fail("REQUIRE_ML=1 but sentence-transformers is not installed", pytrace=False)
    pytest.skip("sentence-transformers not installed (runs in Docker/CI)", allow_module_level=True)

from app.core.config import Settings  # noqa: E402
from app.retrieval.hybrid import RetrievedChunk  # noqa: E402
from app.retrieval.rerank import CrossEncoderReranker, get_reranker  # noqa: E402


def _chunk(content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        filename="contract.pdf",
        page=1,
        content=content,
        flagged_injection=False,
        score=score,
        vec_rank=1,
        fts_rank=1,
    )


@pytest.fixture(scope="module")
def reranker() -> CrossEncoderReranker:
    loaded = get_reranker(Settings(_env_file=None, reranker="cross_encoder"))
    assert isinstance(loaded, CrossEncoderReranker)
    return loaded


def test_the_clause_that_answers_beats_the_one_that_shares_the_words(reranker) -> None:
    notice = _chunk(
        "12.2 Notices. Any notice of late delivery, a missed delivery window or a "
        "delivery dispute shall be sent in writing to the addresses in Exhibit A.",
        0.03,  # RRF puts it first: it repeats "late delivery" twice
    )
    penalty = _chunk(
        "4.3 Liquidated damages. For each business day a purchase order is delayed, "
        "Supplier shall pay 1.5% of the value of the delayed tires, up to 15%.",
        0.02,
    )
    reranked = reranker.rerank("What is the penalty for late tire delivery?", [notice, penalty])
    assert reranked[0].content == penalty.content
    assert reranked[0].rerank_score > reranked[1].rerank_score


def test_scores_are_deterministic(reranker) -> None:
    chunks = [_chunk("Diesel is priced at the EIA weekly index.", 0.02), _chunk("Tires.", 0.01)]
    first = reranker.rerank("How is diesel priced?", chunks)
    second = reranker.rerank("How is diesel priced?", chunks)
    assert [c.rerank_score for c in first] == [c.rerank_score for c in second]

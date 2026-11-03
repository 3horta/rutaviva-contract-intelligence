import uuid

from app.retrieval.hybrid import RetrievedChunk
from app.retrieval.ranking import select_chunks


def _chunk(document_id: uuid.UUID, content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id,
        filename="contract.pdf",
        page=1,
        content=content,
        flagged_injection=False,
        score=score,
        vec_rank=1,
        fts_rank=None,
    )


def test_near_duplicates_keep_only_the_best_scored_copy() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    best = _chunk(doc_a, "Supplier shall report any accident within two hours.", 0.9)
    copy = _chunk(doc_b, "Supplier shall report any accident within two hours", 0.5)
    other = _chunk(doc_b, "The fuel surcharge follows the EIA diesel index.", 0.4)
    assert select_chunks([copy, other, best], k=8, max_per_document=3) == [best, other]


def test_chunks_per_document_are_capped() -> None:
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    many = [_chunk(doc_a, f"clause number {i} of the agreement", 1 - i / 10) for i in range(5)]
    exhibit = _chunk(doc_b, "the exhibit prevails over the agreement", 0.01)
    selected = select_chunks([*many, exhibit], k=8, max_per_document=2)
    assert selected == [many[0], many[1], exhibit]


def test_at_most_k_results() -> None:
    doc = uuid.uuid4()
    chunks = [_chunk(doc, f"distinct text {i}", 1 / (i + 1)) for i in range(10)]
    assert len(select_chunks(chunks, k=3, max_per_document=10)) == 3

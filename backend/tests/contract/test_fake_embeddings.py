import math
import os
import subprocess
import sys

from app.providers.embeddings.fake import FakeEmbeddings


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_have_the_schema_dimension_and_unit_norm() -> None:
    emb = FakeEmbeddings(dim=768)
    for vector in emb.embed_documents(["late delivery penalty", "", "the of and"]):
        assert len(vector) == 768
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_similar_texts_are_closer_than_unrelated_ones() -> None:
    emb = FakeEmbeddings(dim=768)
    query = emb.embed_query("penalty for late tire deliveries")
    related = emb.embed_query("Supplier pays a penalty when a tire delivery is late.")
    unrelated = emb.embed_query("The insurance policy covers cargo up to $250,000.")
    assert _cosine(query, related) > _cosine(query, unrelated)


def test_same_vector_across_processes_with_different_hash_seeds() -> None:
    # API (query) and worker (documents) are different processes: a salted hash would
    # silently give different vectors for the same text.
    code = (
        "from app.providers.embeddings.fake import FakeEmbeddings;"
        "print(FakeEmbeddings(768).embed_query('fuel surcharge index')[:8])"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outputs) == 1

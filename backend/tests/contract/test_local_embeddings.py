"""Contract test of the real local embedding model.

Needs the 'ml' dependency group and the model weights, so it only runs on Linux (Docker
image, CI). Elsewhere it is skipped — unless REQUIRE_ML=1 (CI), where a skip would hide
that the real model was never exercised, so it fails instead.
"""

import math
import os
from importlib.util import find_spec

import pytest

if find_spec("sentence_transformers") is None:
    if os.getenv("REQUIRE_ML") == "1":
        pytest.fail("REQUIRE_ML=1 but sentence-transformers is not installed", pytrace=False)
    pytest.skip("sentence-transformers not installed (runs in Docker/CI)", allow_module_level=True)

from app.providers.embeddings.local import LocalEmbeddings  # noqa: E402


@pytest.fixture(scope="module")
def model() -> LocalEmbeddings:
    return LocalEmbeddings("BAAI/bge-base-en-v1.5", 768)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_dimension_and_unit_norm(model: LocalEmbeddings) -> None:
    for vector in [*model.embed_documents(["Fuel surcharge index."]), model.embed_query("x")]:
        assert len(vector) == 768
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-5)


def test_same_text_same_vector(model: LocalEmbeddings) -> None:
    assert model.embed_documents(["Detention is paid per hour."]) == model.embed_documents(
        ["Detention is paid per hour."]
    )


def test_paraphrase_is_closer_than_unrelated_text(model: LocalEmbeddings) -> None:
    # Shares almost no words with the clause: only a real model gets this right.
    query = model.embed_query("How can we exit the agreement early?")
    clause = model.embed_documents(
        ["Either Party may terminate it for convenience with ninety (90) days' written notice."]
    )[0]
    unrelated = model.embed_documents(["Steer tires are removed at 4/32 of an inch."])[0]
    assert _cosine(query, clause) > _cosine(query, unrelated)


def test_tokens_are_counted_not_estimated(model: LocalEmbeddings) -> None:
    assert model.tokens_estimated is False
    assert model.count_tokens(["late delivery penalty"]) > 0

"""Gemini embeddings through a scripted `litellm` module: normalisation, batches, task
types and error mapping. No network."""

import math

import pytest

from app.core.errors import PermanentProviderError, TransientProviderError
from app.providers import get_embeddings
from app.providers.embeddings.gemini import DOCUMENT_TASK, MAX_BATCH, QUERY_TASK, GeminiEmbeddings
from tests import fake_litellm as fl

DIM = 768


def _vector(scale: float) -> list[float]:
    # Gemini's truncated vectors are not unit length: norm = scale * sqrt(DIM).
    return [scale] * DIM


def _echo(**kwargs):
    """Reply with one un-normalised vector per input text."""
    return fl.embedding_response([_vector(i + 2.0) for i in range(len(kwargs["input"]))])


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))


def test_vectors_are_l2_normalised(fake_litellm) -> None:
    fake_litellm.replies.append(_echo)
    vectors = GeminiEmbeddings("gemini-embedding-001", "k", DIM).embed_documents(["a", "b"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == DIM
        assert math.isclose(_norm(vector), 1.0, rel_tol=1e-9)


def test_documents_and_queries_use_their_task_types(fake_litellm) -> None:
    fake_litellm.replies.extend([_echo, _echo])
    embeddings = GeminiEmbeddings("gemini-embedding-001", "k", DIM)
    embeddings.embed_documents(["clause"])
    embeddings.embed_query("question?")

    document_call, query_call = fake_litellm.calls
    assert document_call["task_type"] == DOCUMENT_TASK
    assert query_call["task_type"] == QUERY_TASK
    for call in fake_litellm.calls:
        assert call["model"] == "gemini/gemini-embedding-001"
        assert call["dimensions"] == DIM
        assert call["api_key"] == "k"
        assert call["num_retries"] == 0


def test_documents_are_sent_in_batches(fake_litellm) -> None:
    fake_litellm.replies.extend([_echo] * 3)
    texts = [f"text {i}" for i in range(5)]
    embeddings = GeminiEmbeddings("gemini-embedding-001", "k", DIM, batch_size=2)
    vectors = embeddings.embed_documents(texts)
    assert [len(call["input"]) for call in fake_litellm.calls] == [2, 2, 1]
    assert len(vectors) == 5


def test_batches_never_exceed_the_gemini_limit(fake_litellm) -> None:
    embeddings = GeminiEmbeddings("gemini-embedding-001", "k", DIM, batch_size=256)
    fake_litellm.replies.extend([_echo] * 3)
    embeddings.embed_documents(["t"] * (MAX_BATCH + 1))
    assert [len(call["input"]) for call in fake_litellm.calls] == [MAX_BATCH, 1]


def test_items_are_put_back_in_input_order(fake_litellm) -> None:
    response = fl.embedding_response([_vector(1.0), [-1.0] * DIM])
    response.data.reverse()  # second text first
    fake_litellm.replies.append(response)
    first, second = GeminiEmbeddings("gemini-embedding-001", "k", DIM).embed_documents(["a", "b"])
    assert first[0] > 0 > second[0]


@pytest.mark.parametrize(
    "vectors",
    [
        [[1.0] * 3072],  # wrong dimension
        [[0.0] * DIM],  # zero vector: no direction to compare
        [],  # fewer vectors than texts
    ],
)
def test_bad_vectors_are_permanent_errors(fake_litellm, vectors) -> None:
    fake_litellm.replies.append(fl.embedding_response(vectors))
    with pytest.raises(PermanentProviderError):
        GeminiEmbeddings("gemini-embedding-001", "k", DIM).embed_documents(["a"])


def test_rate_limit_is_transient_with_retry_after(fake_litellm) -> None:
    fake_litellm.replies.append(fl.RateLimitError("429", headers={"retry-after": "12"}))
    with pytest.raises(TransientProviderError) as exc_info:
        GeminiEmbeddings("gemini-embedding-001", "k", DIM).embed_query("q")
    assert exc_info.value.retry_after_s == 12.0


def test_bad_request_is_permanent(fake_litellm) -> None:
    fake_litellm.replies.append(fl.BadRequestError("400"))
    with pytest.raises(PermanentProviderError):
        GeminiEmbeddings("gemini-embedding-001", "k", DIM).embed_documents(["a"])


def test_tokens_are_estimated_and_flagged(fake_litellm) -> None:
    embeddings = GeminiEmbeddings("gemini-embedding-001", "k", DIM)
    assert embeddings.tokens_estimated is True
    assert embeddings.count_tokens(["12345678"]) == 2


def test_factory_builds_gemini_with_the_settings_model_id(make_settings, fake_litellm) -> None:
    settings = make_settings(EMBEDDINGS_PROVIDER="gemini", GEMINI_API_KEY="k")
    embeddings = get_embeddings(settings)
    assert isinstance(embeddings, GeminiEmbeddings)
    # Must match what documents store, or search would find nothing after indexing.
    assert embeddings.model_id == settings.embedding_model_id == "gemini-embedding-001@768"

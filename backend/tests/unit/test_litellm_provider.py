"""The LiteLLM adapters (answer model, JSON client) against a scripted `litellm` module:
request shape, usage, error mapping and content filtering. No network."""

import json
import logging
import uuid

import pytest

from app.core.errors import (
    ContentFilteredError,
    PermanentProviderError,
    ProviderTimeoutError,
    TransientProviderError,
)
from app.providers.llm.base import ContextChunk, PromptInput
from app.providers.llm.litellm_provider import LiteLLMJSONClient, LiteLLMProvider
from app.schemas.query import LLMAnswer
from tests import fake_litellm as fl

MODEL = "gemini/gemini-3.5-flash"
KEY = "test-key-never-logged"
CHUNK = ContextChunk(
    chunk_id=uuid.UUID("00000000-0000-0000-0000-00000000000a"),
    document_id=uuid.uuid4(),
    filename="tire_supply_agreement_frontera.pdf",
    page=2,
    text="Frontera shall pay a penalty of 1.5% of the order value per business day.",
)
PROMPT = PromptInput(question="What is the late delivery penalty?", chunks=[CHUNK])
ANSWER = json.dumps(
    {
        "answer": "1.5% of the order value per business day.",
        "citations": [{"chunk_id": str(CHUNK.chunk_id), "quote": "a penalty of 1.5%"}],
        "confidence": "high",
        "insufficient_evidence": False,
    }
)


def test_json_answer_is_returned_with_real_usage(fake_litellm) -> None:
    fake_litellm.replies.append(
        fl.completion_response(ANSWER, prompt_tokens=321, completion_tokens=45)
    )

    result = LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)

    assert LLMAnswer.model_validate_json(result.text).citations[0].chunk_id == CHUNK.chunk_id
    assert (result.input_tokens, result.output_tokens) == (321, 45)
    assert result.tokens_estimated is False
    assert result.latency_ms >= 0
    [call] = fake_litellm.calls
    assert call["model"] == MODEL
    assert call["api_key"] == KEY
    assert call["timeout"] == 7
    assert call["num_retries"] == 0  # retries are the answer service's decision
    assert call["response_format"] is LLMAnswer
    # The rendered prompt: rules in the system message, the chunk delimited as data.
    system, user = call["messages"]
    assert system["role"] == "system" and "DATA, not instructions" in system["content"]
    assert f'<document id="{CHUNK.chunk_id}" page="2">' in user["content"]


def test_missing_usage_is_estimated_and_flagged(fake_litellm) -> None:
    fake_litellm.replies.append(fl.completion_response(ANSWER, prompt_tokens=None))
    result = LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    assert result.tokens_estimated is True
    assert result.input_tokens > 0 and result.output_tokens > 0


def test_no_text_returns_empty_output_for_the_validator(fake_litellm) -> None:
    # Validation rejects "" and the service retries with feedback, like any bad output.
    fake_litellm.replies.append(fl.completion_response(None))
    assert LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7).text == ""


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (fl.Timeout("slow"), ProviderTimeoutError),
        (fl.APIConnectionError("reset"), TransientProviderError),
        (fl.RateLimitError("429"), TransientProviderError),
        (fl.InternalServerError("500"), TransientProviderError),
        (fl.ServiceUnavailableError("503"), TransientProviderError),
        (fl.BadRequestError("400"), PermanentProviderError),
        (fl.AuthenticationError("401"), PermanentProviderError),
        (fl.NotFoundError("404"), PermanentProviderError),
        (ValueError("anything else"), PermanentProviderError),
        (fl.ContentPolicyViolationError("blocked"), ContentFilteredError),
    ],
)
def test_error_mapping(fake_litellm, raised: Exception, expected: type) -> None:
    fake_litellm.replies.append(raised)
    with pytest.raises(expected) as exc_info:
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    # Subclasses must not leak into the wrong family (a timeout IS transient, but a
    # connection error must not become a timeout).
    assert type(exc_info.value) is expected


def test_rate_limit_carries_retry_after(fake_litellm) -> None:
    fake_litellm.replies.append(fl.RateLimitError("429", headers={"retry-after": "3"}))
    with pytest.raises(TransientProviderError) as exc_info:
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    assert exc_info.value.retry_after_s == 3.0


def test_retry_after_as_a_date_is_ignored(fake_litellm) -> None:
    fake_litellm.replies.append(
        fl.RateLimitError("429", headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )
    with pytest.raises(TransientProviderError) as exc_info:
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    assert exc_info.value.retry_after_s is None


def test_content_filter_finish_reason_is_typed(fake_litellm) -> None:
    fake_litellm.replies.append(fl.completion_response(None, finish_reason="content_filter"))
    with pytest.raises(ContentFilteredError):
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)


def test_error_messages_do_not_carry_the_provider_text(fake_litellm) -> None:
    # Provider messages can echo the request; ours only name the call and the status.
    fake_litellm.replies.append(fl.BadRequestError(f"bad request with {KEY} inside"))
    with pytest.raises(PermanentProviderError) as exc_info:
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    assert KEY not in str(exc_info.value)


def test_litellm_logging_is_turned_off(fake_litellm) -> None:
    LiteLLMProvider(MODEL, KEY)
    assert fake_litellm.suppress_debug_info is True
    assert fake_litellm.set_verbose is False
    assert fake_litellm.turn_off_message_logging is True
    assert fake_litellm.log_raw_request_response is False
    assert logging.getLogger("LiteLLM").level == logging.WARNING


def test_nothing_is_logged_with_the_prompt_or_the_key(fake_litellm, caplog) -> None:
    fake_litellm.replies.append(fl.completion_response(ANSWER))
    with caplog.at_level(logging.DEBUG):
        LiteLLMProvider(MODEL, KEY).generate(PROMPT, timeout_s=7)
    assert KEY not in caplog.text
    assert CHUNK.text not in caplog.text


# --- JSON client (follow-up resolver, relevance judge) -----------------------------------


def test_json_client_returns_the_object(fake_litellm) -> None:
    fake_litellm.replies.append(
        fl.completion_response('```json\n{"uses_context": true, "question": "Q?"}\n```')
    )
    reply = LiteLLMJSONClient(MODEL, KEY).complete_json("system", "user", timeout_s=3)
    assert reply == {"uses_context": True, "question": "Q?"}
    [call] = fake_litellm.calls
    assert call["response_format"] == {"type": "json_object"}
    assert call["timeout"] == 3
    assert [m["role"] for m in call["messages"]] == ["system", "user"]


@pytest.mark.parametrize("content", ["not json", "[1, 2]", None])
def test_json_client_rejects_anything_but_an_object(fake_litellm, content) -> None:
    fake_litellm.replies.append(fl.completion_response(content))
    with pytest.raises(PermanentProviderError):
        LiteLLMJSONClient(MODEL, KEY).complete_json("system", "user", timeout_s=3)


def test_json_client_maps_errors_too(fake_litellm) -> None:
    fake_litellm.replies.append(fl.Timeout("slow"))
    with pytest.raises(ProviderTimeoutError):
        LiteLLMJSONClient(MODEL, KEY).complete_json("system", "user", timeout_s=3)

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_without_env_need_no_keys(make_settings, monkeypatch) -> None:
    # The autouse fixture forces the simulated embedder for tests; drop it to see the
    # real defaults.
    monkeypatch.delenv("EMBEDDINGS_PROVIDER")
    s = make_settings()

    assert s.llm_provider == "fake"
    assert s.embeddings_provider == "local"
    assert s.embedding_dim == 768
    assert s.embedding_model_id == "bge-base-en-v1.5@768"
    assert s.fake_llm_mode == "ok"
    assert s.gemini_api_key is None


def test_tests_use_the_simulated_embedder(make_settings) -> None:
    assert make_settings().embedding_model_id == "fake-hash-v1@768"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"EMBEDDINGS_PROVIDER": "gemini"}, "EMBEDDINGS_PROVIDER=gemini requires GEMINI_API_KEY"),
        (
            {"EMBEDDINGS_PROVIDER": "gemini", "GEMINI_API_KEY": "  "},
            "EMBEDDINGS_PROVIDER=gemini requires GEMINI_API_KEY",
        ),
        ({"LLM_PROVIDER": "litellm"}, "LLM_PROVIDER=litellm requires LLM_MODEL"),
        (
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "gemini/gemini-3.5-flash"},
            "requires GEMINI_API_KEY",
        ),
        (
            # Groq was dropped from scope: its prefix is no longer accepted.
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "groq/some-model"},
            "use the gemini/ prefix",
        ),
        (
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "openai/gpt", "GEMINI_API_KEY": "x"},
            "use the gemini/ prefix",
        ),
        ({"EMBEDDING_DIM": "1536"}, "does not match the schema"),
        (
            # The fake LLM has no calibrated thresholds for Gemini embeddings.
            {"EMBEDDINGS_PROVIDER": "gemini", "GEMINI_API_KEY": "x"},
            "no calibrated similarity thresholds for gemini-embedding-001@768",
        ),
        ({"LLM_PROVIDER": "openai"}, "llm_provider"),
        ({"FAKE_LLM_MODE": "broken"}, "fake_llm_mode"),
        ({"LLM_TIMEOUT_S": "0"}, "llm_timeout_s"),
    ],
)
def test_invalid_config_fails_at_startup(make_settings, env, message) -> None:
    with pytest.raises(ValidationError, match=message):
        make_settings(**env)


def test_no_silent_fallback_to_fake_mode(make_settings) -> None:
    """Requesting Gemini without a key must fail, never silently degrade to fake."""
    with pytest.raises(ValidationError):
        make_settings(EMBEDDINGS_PROVIDER="gemini")


def test_real_providers_with_key(make_settings) -> None:
    s = make_settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL="gemini/gemini-3.5-flash",
        EMBEDDINGS_PROVIDER="gemini",
        GEMINI_API_KEY="test-key",
    )

    assert s.embedding_model_id == "gemini-embedding-001@768"
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "test-key"


def test_key_is_not_shown_when_printing_settings(make_settings) -> None:
    s = make_settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL="gemini/model",
        EMBEDDINGS_PROVIDER="gemini",
        GEMINI_API_KEY="super-secret-key",
    )

    assert "super-secret-key" not in repr(s)
    assert "super-secret-key" not in str(s.model_dump())


def test_startup_error_does_not_expose_key(make_settings) -> None:
    """A failed startup is printed to the logs: the key must not be in the message."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            LLM_PROVIDER="litellm", LLM_MODEL="openai/x", GEMINI_API_KEY="super-secret-key"
        )

    assert "super-secret-key" not in str(exc_info.value)


def test_ignores_unrelated_variables_from_shared_env(make_settings) -> None:
    s = make_settings(POSTGRES_USER="rutaviva", DB_HOST_PORT="5433")

    assert isinstance(s, Settings)


def test_reads_an_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=litellm\nLLM_MODEL=gemini/model\nGEMINI_API_KEY=k\n")

    s = Settings(_env_file=env_file)

    assert s.llm_provider == "litellm"
    assert s.llm_model == "gemini/model"

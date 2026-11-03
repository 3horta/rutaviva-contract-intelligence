import pytest
from fastapi.testclient import TestClient

from app.core.auth import AuthConfigError
from app.main import create_app


def test_health_in_fake_mode(make_settings) -> None:
    client = TestClient(create_app(make_settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "llm_provider": "fake",
        "llm_model": None,
        "embeddings_provider": "fake",
        "embedding_model": "fake-hash-v1@768",
    }


def test_health_shows_real_providers_without_exposing_keys(make_settings) -> None:
    settings = make_settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL="gemini/gemini-3.5-flash",
        EMBEDDINGS_PROVIDER="gemini",
        GEMINI_API_KEY="super-secret-key",
    )
    client = TestClient(create_app(settings))

    response = client.get("/health")

    body = response.json()
    assert body["llm_provider"] == "litellm"
    assert body["llm_model"] == "gemini/gemini-3.5-flash"
    assert body["embedding_model"] == "gemini-embedding-001@768"
    assert "super-secret-key" not in response.text


def test_app_does_not_start_without_a_signing_key(make_settings, monkeypatch) -> None:
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(AuthConfigError):
        create_app(make_settings(JWT_SECRET_FILE="/nonexistent/jwt_secret"))

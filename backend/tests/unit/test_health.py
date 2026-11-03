from fastapi.testclient import TestClient

from app.main import create_app


def test_health_en_modo_falso(make_settings) -> None:
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


def test_health_muestra_proveedores_reales_sin_exponer_claves(make_settings) -> None:
    settings = make_settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL="gemini/gemini-3.5-flash",
        EMBEDDINGS_PROVIDER="gemini",
        GEMINI_API_KEY="clave-super-secreta",
    )
    client = TestClient(create_app(settings))

    response = client.get("/health")

    body = response.json()
    assert body["llm_provider"] == "litellm"
    assert body["llm_model"] == "gemini/gemini-3.5-flash"
    assert body["embedding_model"] == "gemini-embedding-001@768"
    assert "clave-super-secreta" not in response.text

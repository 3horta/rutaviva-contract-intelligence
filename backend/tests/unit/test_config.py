import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_sin_variables_arranca_en_modo_falso(make_settings) -> None:
    s = make_settings()

    assert s.llm_provider == "fake"
    assert s.embeddings_provider == "fake"
    assert s.embedding_dim == 768
    assert s.embedding_model_id == "fake-hash-v1@768"
    assert s.fake_llm_mode == "ok"
    assert s.gemini_api_key is None


@pytest.mark.parametrize(
    ("env", "mensaje"),
    [
        ({"EMBEDDINGS_PROVIDER": "gemini"}, "EMBEDDINGS_PROVIDER=gemini requiere GEMINI_API_KEY"),
        (
            {"EMBEDDINGS_PROVIDER": "gemini", "GEMINI_API_KEY": "  "},
            "EMBEDDINGS_PROVIDER=gemini requiere GEMINI_API_KEY",
        ),
        ({"LLM_PROVIDER": "litellm"}, "LLM_PROVIDER=litellm requiere LLM_MODEL"),
        (
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "gemini/gemini-3.5-flash"},
            "requiere GEMINI_API_KEY",
        ),
        (
            # Groq quedó fuera del alcance: su prefijo ya no se acepta.
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "groq/algun-modelo"},
            "usa el prefijo gemini/",
        ),
        (
            {"LLM_PROVIDER": "litellm", "LLM_MODEL": "openai/gpt", "GEMINI_API_KEY": "x"},
            "usa el prefijo gemini/",
        ),
        ({"EMBEDDING_DIM": "1536"}, "no coincide con el esquema"),
        ({"LLM_PROVIDER": "openai"}, "llm_provider"),
        ({"FAKE_LLM_MODE": "roto"}, "fake_llm_mode"),
        ({"LLM_TIMEOUT_S": "0"}, "llm_timeout_s"),
    ],
)
def test_configuracion_invalida_falla_al_arrancar(make_settings, env, mensaje) -> None:
    with pytest.raises(ValidationError, match=mensaje):
        make_settings(**env)


def test_sin_fallback_silencioso_a_modo_falso(make_settings) -> None:
    """Pedir Gemini sin clave debe fallar, nunca degradar a falso por su cuenta."""
    with pytest.raises(ValidationError):
        make_settings(EMBEDDINGS_PROVIDER="gemini")


def test_proveedores_reales_con_clave(make_settings) -> None:
    s = make_settings(
        LLM_PROVIDER="litellm",
        LLM_MODEL="gemini/gemini-3.5-flash",
        EMBEDDINGS_PROVIDER="gemini",
        GEMINI_API_KEY="clave-de-prueba",
    )

    assert s.embedding_model_id == "gemini-embedding-001@768"
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "clave-de-prueba"


def test_la_clave_no_aparece_al_imprimir_la_configuracion(make_settings) -> None:
    s = make_settings(EMBEDDINGS_PROVIDER="gemini", GEMINI_API_KEY="clave-super-secreta")

    assert "clave-super-secreta" not in repr(s)
    assert "clave-super-secreta" not in str(s.model_dump())


def test_el_error_de_arranque_no_expone_la_clave(make_settings) -> None:
    """Un arranque fallido se imprime en los logs: la clave no puede ir en el mensaje."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            LLM_PROVIDER="litellm", LLM_MODEL="openai/x", GEMINI_API_KEY="clave-super-secreta"
        )

    assert "clave-super-secreta" not in str(exc_info.value)


def test_ignora_variables_ajenas_del_env_compartido(make_settings) -> None:
    s = make_settings(POSTGRES_USER="rutaviva", DB_HOST_PORT="5433")

    assert isinstance(s, Settings)


def test_lee_un_archivo_env(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=litellm\nLLM_MODEL=gemini/modelo\nGEMINI_API_KEY=k\n")

    s = Settings(_env_file=env_file)

    assert s.llm_provider == "litellm"
    assert s.llm_model == "gemini/modelo"

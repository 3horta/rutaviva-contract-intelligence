"""Configuración de la aplicación desde variables de entorno.

Garantías (sección 8.1 del plan):
- Sin ninguna variable ni `.env`, todo funciona en modo falso.
- Sin fallback silencioso: un proveedor real sin su clave impide arrancar (fail fast).
- Las claves son `SecretStr`: no aparecen en `repr`, logs ni `/health`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> raíz del repo. En Docker apunta a "/.env", que no existe:
# allí las variables llegan por Compose.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

# Docker secrets se montan como archivos en /run/secrets; solo se activa si existe,
# porque pydantic-settings avisa cuando el directorio no está.
_SECRETS_DIR = Path("/run/secrets")

# El esquema de la base fija `vector(768)` (sección 6). Cambiarlo exige una migración.
SCHEMA_EMBEDDING_DIM = 768

LLMProvider = Literal["fake", "litellm"]
EmbeddingsProvider = Literal["fake", "gemini"]
FakeLLMMode = Literal["ok", "invalid", "invalid_once", "timeout", "empty"]

FAKE_EMBEDDING_MODEL = "fake-hash-v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
        # El .env compartido también trae variables de Compose (POSTGRES_*, *_HOST_PORT).
        extra="ignore",
        # Sin esto, el ValidationError incluye `input_value` con todas las variables,
        # claves incluidas, y acabarían en los logs de un arranque fallido.
        hide_input_in_errors=True,
    )

    # Infraestructura. Los predeterminados apuntan a los puertos que Compose publica
    # en localhost; dentro de Compose se sobrescriben con los nombres de servicio.
    #
    # Dos roles (D-021): la API y el worker usan `rutaviva_app`, que no es superusuario
    # ni dueño de las tablas, para que RLS se aplique. Alembic y el seed usan el dueño.
    # La migración inicial crea `rutaviva_app` con el usuario y la contraseña de
    # `database_url`, así que esa URL es la única fuente de esas credenciales.
    database_url: str = "postgresql+psycopg://rutaviva_app:rutaviva_app@localhost:5433/rutaviva"
    migrations_database_url: str = "postgresql+psycopg://rutaviva:rutaviva@localhost:5433/rutaviva"
    redis_url: str = "redis://localhost:6379/0"

    # Proveedores (predeterminado: modo falso)
    llm_provider: LLMProvider = "fake"
    llm_model: str = ""
    embeddings_provider: EmbeddingsProvider = "fake"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = SCHEMA_EMBEDDING_DIM
    fake_llm_mode: FakeLLMMode = "ok"
    llm_timeout_s: float = Field(default=20.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Claves (solo en .env o Docker secrets, nunca versionadas)
    gemini_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_providers(self) -> Self:
        errors: list[str] = []

        if self.embedding_dim != SCHEMA_EMBEDDING_DIM:
            errors.append(
                f"EMBEDDING_DIM={self.embedding_dim} no coincide con el esquema "
                f"(vector({SCHEMA_EMBEDDING_DIM})); cambiarlo requiere una migración"
            )

        if self.llm_provider == "litellm":
            if not self.llm_model:
                errors.append("LLM_PROVIDER=litellm requiere LLM_MODEL (p. ej. gemini/<modelo>)")
            elif self.llm_model.startswith("gemini/"):
                if not _has_value(self.gemini_api_key):
                    errors.append(f"LLM_MODEL={self.llm_model} requiere GEMINI_API_KEY")
            else:
                errors.append(
                    f"LLM_MODEL={self.llm_model} no está soportado: usa el prefijo gemini/"
                )

        if self.embeddings_provider == "gemini" and not _has_value(self.gemini_api_key):
            errors.append("EMBEDDINGS_PROVIDER=gemini requiere GEMINI_API_KEY")

        if errors:
            raise ValueError("Configuración inválida: " + "; ".join(errors))
        return self

    @property
    def embedding_model_id(self) -> str:
        """Versión del embedding que se guarda por documento (sección 8.3)."""
        name = (
            FAKE_EMBEDDING_MODEL
            if self.embeddings_provider == "fake"
            else self.gemini_embedding_model
        )
        return f"{name}@{self.embedding_dim}"


def _has_value(secret: SecretStr | None) -> bool:
    return secret is not None and secret.get_secret_value().strip() != ""


@lru_cache
def get_settings() -> Settings:
    return Settings()

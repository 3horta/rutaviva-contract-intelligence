"""Application settings from environment variables.

Guarantees:
- With no variables and no `.env`, everything runs in fake mode.
- No silent fallback: a real provider without its key prevents startup (fail fast).
- Keys are `SecretStr`: they never appear in `repr`, logs or `/health`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root. In Docker this points to "/.env", which does
# not exist: there the variables come from Compose.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

# Docker secrets are mounted as files under /run/secrets; only enabled if it exists,
# because pydantic-settings warns when the directory is missing.
_SECRETS_DIR = Path("/run/secrets")

# The database schema fixes `vector(768)`. Changing it requires a migration.
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
        # The shared .env also holds Compose variables (POSTGRES_*, *_HOST_PORT).
        extra="ignore",
        # Without this, ValidationError includes `input_value` with every variable,
        # keys included, and they would end up in the logs of a failed startup.
        hide_input_in_errors=True,
    )

    # Infrastructure. Defaults point to the ports Compose publishes on localhost;
    # inside Compose they are overridden with the service names.
    #
    # Two roles: the API and the worker use `rutaviva_app`, which is neither superuser
    # nor table owner, so RLS applies. Alembic and the seed use the owner.
    # The initial migration creates `rutaviva_app` with the user and password from
    # `database_url`, so that URL is the single source of those credentials.
    database_url: str = "postgresql+psycopg://rutaviva_app:rutaviva_app@localhost:5433/rutaviva"
    migrations_database_url: str = "postgresql+psycopg://rutaviva:rutaviva@localhost:5433/rutaviva"
    redis_url: str = "redis://localhost:6379/0"

    # Providers (default: fake mode)
    llm_provider: LLMProvider = "fake"
    llm_model: str = ""
    embeddings_provider: EmbeddingsProvider = "fake"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = SCHEMA_EMBEDDING_DIM
    fake_llm_mode: FakeLLMMode = "ok"
    llm_timeout_s: float = Field(default=20.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Keys (only in .env or Docker secrets, never committed)
    gemini_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_providers(self) -> Self:
        errors: list[str] = []

        if self.embedding_dim != SCHEMA_EMBEDDING_DIM:
            errors.append(
                f"EMBEDDING_DIM={self.embedding_dim} does not match the schema "
                f"(vector({SCHEMA_EMBEDDING_DIM})); changing it requires a migration"
            )

        if self.llm_provider == "litellm":
            if not self.llm_model:
                errors.append("LLM_PROVIDER=litellm requires LLM_MODEL (e.g. gemini/<model>)")
            elif self.llm_model.startswith("gemini/"):
                if not _has_value(self.gemini_api_key):
                    errors.append(f"LLM_MODEL={self.llm_model} requires GEMINI_API_KEY")
            else:
                errors.append(
                    f"LLM_MODEL={self.llm_model} is not supported: use the gemini/ prefix"
                )

        if self.embeddings_provider == "gemini" and not _has_value(self.gemini_api_key):
            errors.append("EMBEDDINGS_PROVIDER=gemini requires GEMINI_API_KEY")

        if errors:
            raise ValueError("Invalid configuration: " + "; ".join(errors))
        return self

    @property
    def embedding_model_id(self) -> str:
        """Embedding version stored with each document."""
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

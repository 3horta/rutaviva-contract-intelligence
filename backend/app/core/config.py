"""Application settings from environment variables.

Guarantees:
- With no variables and no `.env`, everything runs without paid keys: a fake LLM and a
  real embedding model that runs locally (no API, no key). Tests use a simulated
  embedder instead (`EMBEDDINGS_PROVIDER=fake`).
- No silent fallback: a real provider without its key prevents startup (fail fast), and
  so does the fake LLM with an embedding model it has no calibrated thresholds for.
- Keys are `SecretStr`: they never appear in `repr`, logs or `/health`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.similarity import calibrated_thresholds

# backend/app/core/config.py -> repo root. In Docker this points to "/.env", which does
# not exist: there the variables come from Compose.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"

# Docker secrets are mounted as files under /run/secrets; only enabled if it exists,
# because pydantic-settings warns when the directory is missing.
_SECRETS_DIR = Path("/run/secrets")

# The database schema fixes `vector(768)`. Changing it requires a migration.
SCHEMA_EMBEDDING_DIM = 768

LLMProvider = Literal["fake", "litellm"]
# local: a real open model run in-process (sentence-transformers); fake: the simulated
# embedder used by the tests; gemini: optional paid API.
EmbeddingsProvider = Literal["local", "fake", "gemini"]
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
    embeddings_provider: EmbeddingsProvider = "local"
    # 768 dimensions, trained for retrieval, English, MIT licence. Baked into the image.
    local_embedding_model: str = "BAAI/bge-base-en-v1.5"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = SCHEMA_EMBEDDING_DIM
    fake_llm_mode: FakeLLMMode = "ok"
    llm_timeout_s: float = Field(default=20.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)

    # Keys (only in .env or Docker secrets, never committed)
    gemini_api_key: SecretStr | None = None

    # Authentication: test JWTs (HS256) carrying the tenant. The signing key is never in
    # the repo: either JWT_SECRET is set, or the `migrate` service generates one into
    # `jwt_secret_file` (a shared volume) on first start and the API reads it from there.
    jwt_secret: SecretStr | None = None
    jwt_secret_file: Path = Path("var/secrets/jwt_secret")
    jwt_issuer: str = "rutaviva"
    jwt_ttl_hours: int = Field(default=24, gt=0, le=24 * 30)

    # Uploads and file storage (local disk behind the Storage port)
    files_dir: Path = Path("var/files")
    max_upload_mb: int = Field(default=20, gt=0, le=100)

    # Ingestion task (Celery). Soft limit raises inside the task so it can mark the
    # document `failed`; the hard limit kills the process (the lease covers that case).
    task_soft_time_limit_s: int = Field(default=240, gt=0)
    task_time_limit_s: int = Field(default=300, gt=0)
    task_max_retries: int = Field(default=3, ge=0, le=10)
    task_retry_backoff_s: int = Field(default=5, ge=1)
    # Deliveries of the same document before it is marked `failed` (`worker_lost`).
    task_max_attempts: int = Field(default=3, ge=1, le=10)
    embedding_batch_size: int = Field(default=32, gt=0, le=256)

    # Text extraction. pdfplumber (default) reads the PDF text layer: seconds per
    # contract, no models. docling adds layout understanding with a model, at minutes per
    # contract on a CPU.
    text_extractor: Literal["pdfplumber", "docling"] = "pdfplumber"

    # Docling models are baked into the worker image; this points at them so the worker
    # never downloads anything at runtime. None = Docling's default cache.
    docling_artifacts_path: Path | None = None

    # Chunking (provisional values; decided with the evaluation set)
    chunk_max_chars: int = Field(default=1000, gt=100)
    chunk_overlap_chars: int = Field(default=150, ge=0)

    # Hybrid retrieval (provisional values; decided with the evaluation set)
    retrieval_candidates: int = Field(default=40, gt=0, le=200)  # per branch
    retrieval_k: int = Field(default=8, gt=0, le=50)
    rrf_k: int = Field(default=60, gt=0)
    rrf_weight_vector: float = Field(default=0.6, ge=0)
    rrf_weight_fts: float = Field(default=0.4, ge=0)
    max_chunks_per_document: int = Field(default=3, gt=0)

    # Observability. No endpoint means spans are created but not exported.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "rutaviva-api"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _validate_providers(self) -> Self:
        errors: list[str] = []

        if self.embedding_dim != SCHEMA_EMBEDDING_DIM:
            errors.append(
                f"EMBEDDING_DIM={self.embedding_dim} does not match the schema "
                f"(vector({SCHEMA_EMBEDDING_DIM})); changing it requires a migration"
            )

        # The fake LLM picks sentences by cosine similarity, and cosine scales depend on
        # the embedding model: without measured thresholds for it, refuse to start.
        if self.llm_provider == "fake" and calibrated_thresholds(self.embedding_model_id) is None:
            errors.append(
                f"LLM_PROVIDER=fake has no calibrated similarity thresholds for "
                f"{self.embedding_model_id}; run scripts/calibrate_extractive.py and add "
                "them to app/core/similarity.py"
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
        names = {
            "local": self.local_embedding_model.rsplit("/", 1)[-1],
            "fake": FAKE_EMBEDDING_MODEL,
            "gemini": self.gemini_embedding_model,
        }
        return f"{names[self.embeddings_provider]}@{self.embedding_dim}"


def _has_value(secret: SecretStr | None) -> bool:
    return secret is not None and secret.get_secret_value().strip() != ""


@lru_cache
def get_settings() -> Settings:
    return Settings()

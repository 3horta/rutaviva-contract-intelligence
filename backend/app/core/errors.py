"""Typed errors shared by the API and the worker.

Two families:
- `AppError`: something the API turns into an RFC 9457 Problem Details response. Each
  subclass fixes its HTTP status; the handler lives in `app/api/problem.py`.
- `IngestionError`: a *permanent* reason a document ends in `failed`, carrying its
  closed `failure_code`. The task never retries these.
- `TransientProviderError` / `PermanentProviderError`: what provider adapters (embeddings,
  LLM) raise, so callers decide "retry" vs "give up" without knowing the provider.
"""

from app.db.models import FailureCode

# --- API errors (Problem Details) -------------------------------------------------------


class AppError(Exception):
    status: int = 500
    title: str = "Internal error"
    # Problem type URI; relative is allowed by RFC 9457.
    type: str = "about:blank"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class Unauthorized(AppError):
    status, title, type = 401, "Unauthorized", "/problems/unauthorized"


class NotFound(AppError):
    # Also used for resources of another tenant: never reveal that they exist.
    status, title, type = 404, "Not found", "/problems/not-found"


class PayloadTooLarge(AppError):
    status, title, type = 413, "File too large", "/problems/file-too-large"


class UnsupportedMediaType(AppError):
    status, title, type = 415, "Unsupported file type", "/problems/unsupported-media-type"


class Unprocessable(AppError):
    status, title, type = 422, "Unprocessable request", "/problems/unprocessable"


class ServiceUnavailable(AppError):
    status, title, type = 503, "Service unavailable", "/problems/service-unavailable"


class GatewayTimeout(AppError):
    status, title, type = 504, "Upstream timeout", "/problems/upstream-timeout"


# --- Ingestion (permanent document failures) --------------------------------------------


class IngestionError(Exception):
    failure_code: FailureCode = FailureCode.INTERNAL_ERROR

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CorruptPDFError(IngestionError):
    failure_code = FailureCode.CORRUPT_PDF


class EmptyPDFError(IngestionError):
    failure_code = FailureCode.EMPTY_PDF


# --- Providers --------------------------------------------------------------------------


class ProviderError(Exception):
    """Base for provider adapter errors. Never carries keys or request bodies."""


class TransientProviderError(ProviderError):
    """Timeouts, 429, 5xx, connection errors: worth retrying with backoff."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderTimeoutError(TransientProviderError):
    """The provider did not answer within the configured timeout (maps to 504)."""


class PermanentProviderError(ProviderError):
    """4xx other than 429, malformed responses, wrong dimension: retrying won't help."""

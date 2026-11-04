"""What the LiteLLM adapters (LLM and embeddings) share: loading the library safely and
turning its exceptions into the provider errors the rest of the code understands.

LiteLLM is only imported through `load_litellm()`, called by the real adapters when
they are created. Keyless mode (fake LLM, local or fake embeddings) never imports it.
"""

import logging
import os
from types import ModuleType

from app.core.errors import (
    ContentFilteredError,
    PermanentProviderError,
    ProviderError,
    ProviderTimeoutError,
    TransientProviderError,
)

# LiteLLM's own loggers. Kept at WARNING so that a DEBUG log level of the app never
# makes them print requests (headers carry the API key).
_LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


def load_litellm() -> ModuleType:
    """Import LiteLLM with every kind of request logging off."""
    # Without this, importing LiteLLM downloads its model price list from GitHub: a
    # network call at startup that the app does not need.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banners
    litellm.set_verbose = False  # no request/response dumps
    litellm.turn_off_message_logging = True  # no prompts or answers in callbacks
    litellm.log_raw_request_response = False
    for name in _LITELLM_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return litellm


def to_provider_error(litellm: ModuleType, exc: Exception, what: str) -> ProviderError:
    """Map a LiteLLM exception to a provider error.

    `what` names the call ("gemini completion", "gemini embedding") for the message. The
    provider's own message is NOT kept: it can echo parts of the request.

    Order matters: `Timeout` is a subclass of `APIConnectionError`, and
    `ContentPolicyViolationError` of `BadRequestError`.
    """
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if isinstance(exc, litellm.Timeout):
        return ProviderTimeoutError(f"{what} timed out")
    if isinstance(exc, litellm.ContentPolicyViolationError):
        return ContentFilteredError(f"{what} blocked by the provider's safety filter")
    if isinstance(exc, litellm.APIConnectionError):
        return TransientProviderError(f"{what} failed to connect ({name})")
    if status == 429 or (isinstance(status, int) and status >= 500):
        return TransientProviderError(
            f"{what} failed with HTTP {status}", retry_after_s=retry_after_s(exc)
        )
    # 400 (bad request, context window), 401/403 (key), 404 (model), anything unknown:
    # retrying the same request will not help.
    return PermanentProviderError(f"{what} failed ({name}, HTTP {status})")


def retry_after_s(exc: Exception) -> float | None:
    """Seconds from a `Retry-After` header, if the provider sent one (as a number).

    LiteLLM keeps headers it attached itself in `exc.headers` and the provider's
    response headers in `exc.response.headers`. An HTTP-date value is ignored: the
    answer service then waits its default.
    """
    response = getattr(exc, "response", None)
    for headers in (getattr(exc, "headers", None), getattr(response, "headers", None)):
        if not headers:
            continue
        value = headers.get("retry-after") or headers.get("Retry-After")
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds >= 0:
            return seconds
    return None

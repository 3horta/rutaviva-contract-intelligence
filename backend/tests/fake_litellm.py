"""A stand-in `litellm` module for unit tests: no network, and no 30-second import.

It has only what the adapters use: `completion`, `embedding`, the logging flags and
the exception classes, with the same hierarchy as LiteLLM's where the error mapping
depends on it (`Timeout` is an `APIConnectionError`, `ContentPolicyViolationError` a
`BadRequestError`) and a `status_code` on each.

Install it with the `fake_litellm` fixture (tests/conftest.py). Each call is recorded
in `calls`; `replies` holds what the next calls return (or raise), in order.
"""

import types
from collections.abc import Callable
from typing import Any


class APIError(Exception):
    status_code = 500

    def __init__(self, message: str = "", headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.headers = headers


class APIConnectionError(APIError):
    pass


class Timeout(APIConnectionError):
    status_code = 408


class BadRequestError(APIError):
    status_code = 400


class ContentPolicyViolationError(BadRequestError):
    pass


class AuthenticationError(APIError):
    status_code = 401


class NotFoundError(APIError):
    status_code = 404


class RateLimitError(APIError):
    status_code = 429


class InternalServerError(APIError):
    status_code = 500


class ServiceUnavailableError(APIError):
    status_code = 503


def completion_response(
    content: str | None,
    *,
    finish_reason: str = "stop",
    prompt_tokens: int | None = 120,
    completion_tokens: int | None = 30,
    model: str = "gemini-3.5-flash",
) -> Any:
    """What `litellm.completion` returns, reduced to the fields the adapter reads."""
    usage = (
        None
        if prompt_tokens is None
        else types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    )
    message = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=message, finish_reason=finish_reason)
    return types.SimpleNamespace(choices=[choice], usage=usage, model=model)


def embedding_response(vectors: list[list[float]]) -> Any:
    """What `litellm.embedding` returns: items with `embedding` and `index`."""
    items = [{"embedding": vector, "index": i} for i, vector in enumerate(vectors)]
    return types.SimpleNamespace(data=items)


class FakeLiteLLM(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("litellm")
        self.calls: list[dict[str, Any]] = []
        # Items: a response, an exception (raised) or a callable(**kwargs) -> response.
        self.replies: list[Any] = []
        self.suppress_debug_info = False
        self.set_verbose = True
        self.turn_off_message_logging = False
        self.log_raw_request_response = True
        for cls in (
            APIError,
            APIConnectionError,
            Timeout,
            BadRequestError,
            ContentPolicyViolationError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
            InternalServerError,
            ServiceUnavailableError,
        ):
            setattr(self, cls.__name__, cls)

    def _reply(self, kind: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append({"kind": kind, **kwargs})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Callable):  # type: ignore[arg-type]
            return item(**kwargs)
        return item

    def completion(self, **kwargs: Any) -> Any:
        return self._reply("completion", kwargs)

    def embedding(self, **kwargs: Any) -> Any:
        return self._reply("embedding", kwargs)

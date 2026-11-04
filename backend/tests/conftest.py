import sys
from collections.abc import Callable

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test depends on the environment or the .env of whoever runs them.

    Tests always use the simulated embedder: the brief requires simulated LLM and
    embeddings in automated tests, and the real local model is only installed in Docker.
    Its adapter has its own contract test (tests/contract/test_local_embeddings.py).
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    # Likewise no cross-encoder: its adapter has a contract test (test_cross_encoder.py).
    monkeypatch.setenv("RERANKER", "none")
    # The API refuses to start without a JWT signing key; tests use a throwaway one.
    monkeypatch.setenv("JWT_SECRET", "test-only-signing-key-0123456789abcdef")
    get_settings.cache_clear()


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Build Settings from the given variables only, without reading any .env."""

    def _make(**env: str) -> Settings:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return _make


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch):
    """Replace the `litellm` module with a scripted stand-in (tests/fake_litellm.py).
    The adapters import it lazily, so they pick this one up."""
    from tests.fake_litellm import FakeLiteLLM

    module = FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module

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
    get_settings.cache_clear()


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """Build Settings from the given variables only, without reading any .env."""

    def _make(**env: str) -> Settings:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return _make

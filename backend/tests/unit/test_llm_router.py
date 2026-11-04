"""Capability routing and the factories: configuration alone picks keyless or real."""

import os
import subprocess
import sys
from pathlib import Path

from app.conversations.resolver import LLMQuestionResolver, RuleBasedResolver, get_resolver
from app.providers.embeddings.fake import FakeEmbeddings
from app.providers.llm import get_json_client, get_llm
from app.providers.llm.fake import FakeLLM
from app.providers.llm.litellm_provider import LiteLLMJSONClient, LiteLLMProvider
from app.providers.llm.router import model_for
from app.retrieval.judge import KeepAll, LLMRelevanceJudge, get_judge

QUALITY = "gemini/gemini-3.5-pro"
FAST = "gemini/gemini-3.5-flash"


def _real(make_settings, **extra: str):
    return make_settings(LLM_PROVIDER="litellm", LLM_MODEL=QUALITY, GEMINI_API_KEY="k", **extra)


def test_router_sends_small_jobs_to_the_fast_model(make_settings) -> None:
    settings = _real(make_settings, LLM_FAST_MODEL=FAST)
    assert model_for(settings, "answer") == QUALITY
    assert model_for(settings, "resolve") == FAST
    assert model_for(settings, "judge") == FAST


def test_without_a_fast_model_everything_uses_llm_model(make_settings) -> None:
    settings = _real(make_settings)
    assert {model_for(settings, c) for c in ("answer", "resolve", "judge")} == {QUALITY}


def test_real_factories_use_the_routed_models(make_settings, fake_litellm) -> None:
    settings = _real(make_settings, LLM_FAST_MODEL=FAST)

    llm = get_llm(settings, FakeEmbeddings(768))
    assert isinstance(llm, LiteLLMProvider) and llm.model_id == QUALITY

    client = get_json_client(settings, "resolve")
    assert isinstance(client, LiteLLMJSONClient) and client.model_id == FAST

    resolver = get_resolver(settings)
    assert isinstance(resolver, LLMQuestionResolver) and resolver.client.model_id == FAST

    judge = get_judge(settings)
    assert isinstance(judge, LLMRelevanceJudge) and judge.client.model_id == FAST


def test_keyless_factories(make_settings) -> None:
    settings = make_settings()
    assert isinstance(get_llm(settings, FakeEmbeddings(768)), FakeLLM)
    assert get_json_client(settings, "judge") is None
    assert isinstance(get_resolver(settings), RuleBasedResolver)
    assert isinstance(get_judge(settings), KeepAll)


def test_keyless_mode_never_imports_litellm() -> None:
    # A fresh process: building the app and every keyless provider must not load LiteLLM
    # (heavy, and it would mean the keyless path depends on it).
    code = (
        "import sys\n"
        "from app.core.config import Settings\n"
        "from app.main import create_app\n"
        "from app.providers import get_embeddings\n"
        "from app.providers.llm import get_llm\n"
        "from app.conversations.resolver import get_resolver\n"
        "from app.retrieval.judge import get_judge\n"
        "s = Settings(_env_file=None)\n"
        "create_app(s)\n"
        "e = get_embeddings(s)\n"
        "get_llm(s, e); get_resolver(s); get_judge(s)\n"
        "print('litellm' in sys.modules)\n"
    )
    env = {
        **{k: v for k, v in os.environ.items() if k != "LLM_PROVIDER"},
        "EMBEDDINGS_PROVIDER": "fake",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=Path(__file__).resolve().parents[2],  # backend/
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "False"

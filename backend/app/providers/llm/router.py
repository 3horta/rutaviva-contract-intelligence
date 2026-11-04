"""Capability router: which model does which job.

A real LLM does three jobs per question, with different needs:
- `answer`: draft the cited answer. Quality matters most: `LLM_MODEL`.
- `resolve`: rewrite a follow-up as a standalone question. Small, latency-bound.
- `judge`: pick which retrieved chunks are actually relevant. Small, latency-bound.

The two small jobs go to `LLM_FAST_MODEL`, which defaults to `LLM_MODEL`: one model is
enough to start, and a cheaper one can be added later by configuration only.
"""

from typing import Literal

from app.core.config import Settings

Capability = Literal["answer", "resolve", "judge"]


def model_for(settings: Settings, capability: Capability) -> str:
    if capability == "answer":
        return settings.llm_model
    return settings.fast_model

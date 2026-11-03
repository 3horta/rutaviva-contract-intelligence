"""Calibrated similarity thresholds of the fake (extractive) LLM, per embedding model.

Cosine scales depend on the model (a retrieval model like BGE compresses most sentences
of the same domain into 0.6-0.9; the simulated hash embedder spreads them over 0-0.5),
so the thresholds are keyed by the full `Settings.embedding_model_id` ("name@dim").

Only measured values live here. `Settings` refuses to start with `LLM_PROVIDER=fake`
when the active embedding model has no entry: an uncalibrated guess would decide, with
no warning, which questions get "not enough information". To add a model, run
`scripts/calibrate_extractive.py` with it and copy the chosen values.

- `minimum`: below it the best sentence is not evidence (`insufficient_evidence`).
- `margin`: sentences within this of the best one are quoted with it.
There is no "high confidence" threshold: the fake LLM declares `high` whenever it
answers (it only quotes verbatim), and the service caps it with its retrieval signal.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SimilarityThresholds:
    minimum: float
    margin: float


CALIBRATED_THRESHOLDS: dict[str, SimilarityThresholds] = {
    # Measured with the unit/contract fixtures: unrelated sentences score <= 0.19,
    # answers 0.30-0.49. Used by every automated test.
    "fake-hash-v1@768": SimilarityThresholds(minimum=0.25, margin=0.05),
}


def calibrated_thresholds(embedding_model_id: str) -> SimilarityThresholds | None:
    return CALIBRATED_THRESHOLDS.get(embedding_model_id)

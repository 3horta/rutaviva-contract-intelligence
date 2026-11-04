"""Metrics: every instrument in one place, and small functions to record them (ADR-020).

Only the OpenTelemetry *API* is used here, as in `tracing.py`: without a configured
MeterProvider every record is a no-op. The SDK and the OTLP exporter are set up once per
process in `app/core/telemetry.py`; the collector exposes them for Prometheus on :8889.

Two families:
- **GenAI semantic conventions** (github.com/open-telemetry/semantic-conventions-genai,
  status development), recorded by `tracing.model_span` for every model call:
  `gen_ai.client.inference.duration`, `gen_ai.client.embeddings.duration` and the
  `gen_ai.client.inference.usage.{input,output}_tokens` counters. Their attributes are
  the ones the conventions allow, nothing else (no `gen_ai.prompt.name`).
- **App metrics** (`rutaviva.*`): answers by outcome, validation failures, prompt
  injection, documents processed, ingestion duration, reranker and judge failures, and
  tokens per prompt template.

Rule: labels are bounded enums only (status, reason, model, prompt template, provider).
Never tenant, document, chunk, conversation or question ids: each value is a new time
series, and ids would grow without limit.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

# Bucket boundaries the GenAI conventions recommend for client durations (seconds).
GENAI_DURATION_BUCKETS = [
    0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92,
]  # fmt: skip
# Ingestion of one document (seconds): from a 1-page PDF to a long one on a CPU.
INGESTION_BUCKETS = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600]

# Attribute names used only by metrics.
GEN_AI_TOKEN_MODALITY = "gen_ai.token.modality"
TOKEN_DIRECTION = "token.direction"  # input | output, on rutaviva.llm.tokens
STATUS = "status"
REASON = "reason"
CONFIDENCE = "confidence"
FAILURE_CODE = "failure_code"
STAGE = "stage"

# The attributes the GenAI duration and token metrics allow (besides `error.type` and
# the token modality); `model_span` passes only these.
GENAI_METRIC_ATTRIBUTES = (
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
)


@dataclass(frozen=True)
class Instruments:
    inference_duration: Histogram
    embeddings_duration: Histogram
    input_tokens: Counter
    output_tokens: Counter
    llm_tokens: Counter
    answers: Counter
    validation_failures: Counter
    prompt_injection: Counter
    documents_processed: Counter
    ingestion_duration: Histogram
    rerank_failures: Counter
    judge_failures: Counter


def build_instruments(meter: Meter) -> Instruments:
    return Instruments(
        inference_duration=meter.create_histogram(
            "gen_ai.client.inference.duration",
            unit="s",
            description="GenAI client inference operation duration.",
            explicit_bucket_boundaries_advisory=GENAI_DURATION_BUCKETS,
        ),
        embeddings_duration=meter.create_histogram(
            "gen_ai.client.embeddings.duration",
            unit="s",
            description="GenAI client embeddings operation duration.",
            explicit_bucket_boundaries_advisory=GENAI_DURATION_BUCKETS,
        ),
        input_tokens=meter.create_counter(
            "gen_ai.client.inference.usage.input_tokens",
            unit="{token}",
            description="The number of input (prompt) tokens used, including cached tokens.",
        ),
        output_tokens=meter.create_counter(
            "gen_ai.client.inference.usage.output_tokens",
            unit="{token}",
            description="The number of output (completion) tokens used.",
        ),
        llm_tokens=meter.create_counter(
            "rutaviva.llm.tokens",
            unit="{token}",
            description="LLM tokens by prompt template and direction (cost per prompt).",
        ),
        answers=meter.create_counter(
            "rutaviva.answers",
            unit="{answer}",
            description="Answers by status, insufficient-evidence reason and confidence.",
        ),
        validation_failures=meter.create_counter(
            "rutaviva.answer.validation_failures",
            unit="{attempt}",
            description="LLM outputs rejected by validation (each is retried or refused).",
        ),
        prompt_injection=meter.create_counter(
            "rutaviva.prompt_injection.flagged",
            unit="{chunk}",
            description="Chunks flagged as prompt injection: at indexing, or excluded "
            "from an answer's context.",
        ),
        documents_processed=meter.create_counter(
            "rutaviva.documents.processed",
            unit="{document}",
            description="Documents whose ingestion ended, by status and failure code.",
        ),
        ingestion_duration=meter.create_histogram(
            "rutaviva.ingestion.duration",
            unit="s",
            description="Ingestion time of one document, by final status.",
            explicit_bucket_boundaries_advisory=INGESTION_BUCKETS,
        ),
        rerank_failures=meter.create_counter(
            "rutaviva.rerank.failures",
            unit="{failure}",
            description="Reranker failures (the fused order was kept).",
        ),
        judge_failures=meter.create_counter(
            "rutaviva.judge.failures",
            unit="{failure}",
            description="Relevance judge failures (every chunk was kept).",
        ),
    )


_instruments = build_instruments(metrics.get_meter("rutaviva"))  # replaced in tests


def instruments() -> Instruments:
    return _instruments


def _clean(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items() if value is not None}


# --- Recording helpers ------------------------------------------------------------------


def record_model_duration(operation: str, seconds: float, attributes: Mapping[str, Any]) -> None:
    """One model call's duration (called by `tracing.model_span`)."""
    histogram = {
        "chat": _instruments.inference_duration,
        "embeddings": _instruments.embeddings_duration,
    }.get(operation)
    if histogram is not None:
        histogram.record(seconds, _clean(attributes))


def record_tokens(
    input_tokens: int | None,
    output_tokens: int | None,
    attributes: Mapping[str, Any],
    *,
    prompt_name: str | None,
) -> None:
    """Token usage of one chat call, as the provider reported it. The conventions only
    allow strictly positive measurements, and modality `unknown` when the provider does
    not split tokens by modality (text, image...): ours do not."""
    genai = {**_clean(attributes), GEN_AI_TOKEN_MODALITY: "unknown"}
    for count, counter, direction in (
        (input_tokens, _instruments.input_tokens, "input"),
        (output_tokens, _instruments.output_tokens, "output"),
    ):
        if count and count > 0:
            counter.add(count, genai)
            _instruments.llm_tokens.add(
                count,
                _clean(
                    {
                        "gen_ai.prompt.name": prompt_name,
                        "gen_ai.request.model": attributes.get("gen_ai.request.model"),
                        TOKEN_DIRECTION: direction,
                    }
                ),
            )


def record_answer(status: str, reason: str | None, confidence: str) -> None:
    _instruments.answers.add(1, _clean({STATUS: status, REASON: reason, CONFIDENCE: confidence}))


def record_validation_failure(reason: str) -> None:
    _instruments.validation_failures.add(1, {REASON: reason})


def record_prompt_injection(chunks: int, *, stage: str) -> None:
    """`stage`: `ingestion` (flagged while indexing) or `query` (excluded from a context)."""
    if chunks > 0:
        _instruments.prompt_injection.add(chunks, {STAGE: stage})


def record_document_processed(status: str, seconds: float, failure_code: str | None) -> None:
    _instruments.documents_processed.add(1, _clean({STATUS: status, FAILURE_CODE: failure_code}))
    _instruments.ingestion_duration.record(seconds, {STATUS: status})


def record_rerank_failure() -> None:
    _instruments.rerank_failures.add(1)


def record_judge_failure() -> None:
    _instruments.judge_failures.add(1)

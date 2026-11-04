"""Metrics (ADR-020), read back with the OpenTelemetry SDK's in-memory reader: GenAI
duration and token metrics from `model_span`, answers by outcome, documents processed,
and no high-cardinality label anywhere."""

import uuid
from typing import Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.core import metrics, tracing
from app.core.errors import ProviderTimeoutError
from app.generation import service
from app.ingestion import pipeline
from app.providers.llm.traced import complete_json_traced
from tests.unit.test_answer_service import CHUNK, ScriptedLLM, _ask, _valid

# Label keys that would make a series per tenant, document, question...
HIGH_CARDINALITY = {"tenant.id", "tenant_id", "document.id", "document_id", "chunk_id", "trace_id"}


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    memory = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[memory])
    monkeypatch.setattr(metrics, "_instruments", metrics.build_instruments(provider.get_meter("t")))
    return memory


def points(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    """Metric name -> its data points (each has `.attributes` and a value)."""
    found: dict[str, list[Any]] = {}
    data = reader.get_metrics_data()
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found


class JSONClient:
    model_id = "gemini/fast"

    def __init__(self, reply: dict | Exception, usage=(50, 4)) -> None:
        self.reply = reply
        self.last_usage = usage

    def complete_json(self, system: str, user: str, *, timeout_s: float) -> dict:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_a_chat_call_records_genai_duration_and_tokens(reader) -> None:
    complete_json_traced(JSONClient({"title": "x"}), "conversation_title", "s", "u", timeout_s=1)

    found = points(reader)
    [duration] = found["gen_ai.client.inference.duration"]
    assert duration.count == 1
    assert dict(duration.attributes) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "gcp.gemini",
        "gen_ai.request.model": "fast",
    }
    [inp] = found["gen_ai.client.inference.usage.input_tokens"]
    [out] = found["gen_ai.client.inference.usage.output_tokens"]
    assert (inp.value, out.value) == (50, 4)
    assert inp.attributes["gen_ai.token.modality"] == "unknown"
    # The conventions do not allow the prompt name on their metrics...
    assert "gen_ai.prompt.name" not in inp.attributes
    # ...so tokens per prompt template are an app metric.
    by_direction = {p.attributes["token.direction"]: p for p in found["rutaviva.llm.tokens"]}
    assert by_direction["input"].value == 50
    assert by_direction["input"].attributes["gen_ai.prompt.name"] == "conversation_title"


def test_a_failed_call_records_its_error_type_and_no_tokens(reader) -> None:
    with pytest.raises(ProviderTimeoutError):
        complete_json_traced(
            JSONClient(ProviderTimeoutError("slow"), usage=None), "relevance_judge", "s", "u",
            timeout_s=1,
        )  # fmt: skip

    found = points(reader)
    [duration] = found["gen_ai.client.inference.duration"]
    assert duration.attributes["error.type"] == "ProviderTimeoutError"
    assert "gen_ai.client.inference.usage.input_tokens" not in found


def test_embeddings_have_their_own_duration_metric(reader) -> None:
    with tracing.model_span(tracing.OP_EMBEDDINGS, "bge-base-en-v1.5@768"):
        pass

    [duration] = points(reader)["gen_ai.client.embeddings.duration"]
    assert duration.attributes["gen_ai.provider.name"] == "rutaviva.local"
    assert duration.attributes["gen_ai.request.model"] == "bge-base-en-v1.5"


def test_answers_are_counted_by_outcome(reader, monkeypatch) -> None:
    monkeypatch.setattr(service, "search", lambda *a, **k: [CHUNK])
    monkeypatch.setattr(service, "_sleep", lambda _s: None)

    _ask(ScriptedLLM("not json", _valid()))  # one validation failure, then answered
    _ask(ScriptedLLM("{}", "{}", "{}"))  # every attempt invalid: insufficient

    found = points(reader)
    answers = {
        (p.attributes["status"], p.attributes.get("reason"), p.attributes["confidence"]): p.value
        for p in found["rutaviva.answers"]
    }
    assert answers == {
        ("answered", None, "high"): 1,
        ("insufficient_evidence", "validation_failed", "low"): 1,
    }
    assert sum(p.value for p in found["rutaviva.answer.validation_failures"]) == 4
    # The answer's chat calls: response model and tokens on the GenAI metrics.
    [inp] = found["gen_ai.client.inference.usage.input_tokens"]
    assert inp.attributes["gen_ai.response.model"] == "scripted"


def test_documents_are_counted_when_they_end(reader, monkeypatch) -> None:
    from app.db.models import FailureCode

    outcomes = iter(
        [
            pipeline.ProcessOutcome("ready", chunk_count=3),
            pipeline.ProcessOutcome("failed", failure_code=FailureCode.EMPTY_PDF),
            pipeline.ProcessOutcome("skipped"),
        ]
    )
    monkeypatch.setattr(pipeline, "_process_document", lambda *a: next(outcomes))
    ticks = iter([0.0, 2.5, 10.0, 11.0, 20.0, 20.5])
    deps = type("Deps", (), {"clock": staticmethod(lambda: next(ticks))})()

    for _ in range(3):
        pipeline.process_document(uuid.uuid4(), uuid.uuid4(), deps)

    found = points(reader)
    processed = {
        (p.attributes["status"], p.attributes.get("failure_code")): p.value
        for p in found["rutaviva.documents.processed"]
    }
    assert processed == {("ready", None): 1, ("failed", "empty_pdf"): 1}  # skipped: not an end
    durations = {p.attributes["status"]: p.sum for p in found["rutaviva.ingestion.duration"]}
    assert durations == {"ready": 2.5, "failed": 1.0}


def test_no_label_identifies_a_tenant_document_or_question(reader, monkeypatch) -> None:
    monkeypatch.setattr(service, "search", lambda *a, **k: [CHUNK])
    _ask(ScriptedLLM(_valid()))
    complete_json_traced(JSONClient({"title": "x"}), "conversation_title", "s", "u", timeout_s=1)
    metrics.record_prompt_injection(2, stage="ingestion")
    metrics.record_document_processed("ready", 1.0, None)

    for name, found in points(reader).items():
        for point in found:
            keys = set(point.attributes)
            assert not keys & HIGH_CARDINALITY, (name, keys)
            for value in point.attributes.values():
                assert not _looks_like_uuid(str(value)), (name, value)


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True

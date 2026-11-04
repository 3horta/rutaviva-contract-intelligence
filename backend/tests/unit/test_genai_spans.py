"""The answer pipeline's traces follow the OpenTelemetry GenAI semantic conventions
(ADR-018): span names, kinds, required attributes, the tree they form, and no content.
"""

import json
import uuid

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app.core import tracing
from app.core.config import Settings
from app.core.errors import ProviderTimeoutError
from app.core.tracing import ModelRef, model_ref
from app.generation import service
from app.ingestion import pipeline
from app.providers.embeddings.fake import FakeEmbeddings
from tests.unit.test_answer_service import CHUNK, ScriptedLLM, _valid

QUESTION = "What is the penalty for late tire delivery?"
CONVERSATION = uuid.uuid4()


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    provider = TracerProvider()
    exported = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exported))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(service, "search", lambda *args, **kwargs: [CHUNK])
    monkeypatch.setattr(service, "_sleep", lambda _s: None)
    return exported


def _answer(llm: ScriptedLLM) -> None:
    service.answer_with_usage(
        QUESTION,
        tenant_id=uuid.uuid4(),
        session=None,  # unused: `search` is stubbed
        embeddings=FakeEmbeddings(768),
        llm=llm,
        settings=Settings(_env_file=None, llm_max_retries=1),
        conversation_id=CONVERSATION,
    )


def _by_name(exporter: InMemorySpanExporter) -> dict[str, list[ReadableSpan]]:
    spans: dict[str, list[ReadableSpan]] = {}
    for span in exporter.get_finished_spans():
        spans.setdefault(span.name, []).append(span)
    return spans


def test_an_answer_is_a_workflow_of_embeddings_retrieval_and_chat(exporter) -> None:
    _answer(ScriptedLLM(_valid()))

    spans = _by_name(exporter)
    [workflow] = spans["invoke_workflow contract_qa"]
    [embed] = spans["embeddings fake-hash-v1"]
    [retrieval] = spans["retrieval"]
    [chat] = spans["chat scripted"]

    assert workflow.kind == SpanKind.INTERNAL
    assert workflow.attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert workflow.attributes["gen_ai.workflow.name"] == "contract_qa"
    assert workflow.attributes["gen_ai.conversation.id"] == str(CONVERSATION)

    assert embed.kind == SpanKind.CLIENT
    assert embed.attributes["gen_ai.operation.name"] == "embeddings"
    assert embed.attributes["gen_ai.provider.name"] == "rutaviva.fake"
    assert embed.attributes["gen_ai.request.model"] == "fake-hash-v1"
    assert embed.attributes["gen_ai.embeddings.dimension.count"] == 768

    assert retrieval.kind == SpanKind.INTERNAL
    assert retrieval.attributes["gen_ai.operation.name"] == "retrieval"
    assert retrieval.attributes["gen_ai.retrieval.top_k"] == 8
    documents = json.loads(retrieval.attributes["gen_ai.retrieval.documents"])
    assert documents == [{"id": str(CHUNK.chunk_id), "score": round(CHUNK.score, 6)}]

    assert chat.kind == SpanKind.CLIENT
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.provider.name"] == "rutaviva.local"
    assert chat.attributes["gen_ai.request.model"] == "scripted"
    assert chat.attributes["gen_ai.response.model"] == "scripted"
    assert chat.attributes["gen_ai.prompt.name"] == "answer"
    assert chat.attributes["gen_ai.output.type"] == "json"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 10
    assert chat.attributes["gen_ai.usage.output_tokens"] == 5

    # One tree: every step is a child of the workflow.
    for child in (embed, retrieval, chat):
        assert child.parent is not None
        assert child.parent.span_id == workflow.context.span_id


def test_each_attempt_is_its_own_chat_span(exporter) -> None:
    _answer(ScriptedLLM("not json", _valid()))

    chats = _by_name(exporter)["chat scripted"]
    assert [c.attributes["llm.retries"] for c in chats] == [0, 1]
    assert chats[0].attributes["llm.validation_error"]
    assert "llm.validation_error" not in chats[1].attributes


def test_a_failed_call_has_an_error_type(exporter) -> None:
    with pytest.raises(Exception):  # noqa: B017 - mapped to a 504 by the service
        _answer(ScriptedLLM(ProviderTimeoutError("slow"), ProviderTimeoutError("slow")))

    chats = _by_name(exporter)["chat scripted"]
    assert chats[-1].attributes["error.type"] == "ProviderTimeoutError"
    assert not chats[-1].status.is_ok


def test_no_content_is_recorded(exporter) -> None:
    _answer(ScriptedLLM(_valid()))

    for span in exporter.get_finished_spans():
        values = " ".join(str(value) for value in span.attributes.values())
        assert QUESTION not in values
        assert CHUNK.content not in values
        assert "1.5% per business day" not in values
        # The opt-in content attributes of the conventions are never set.
        assert not {
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.retrieval.query.text",
        } & set(span.attributes)


def test_ingestion_embeds_each_batch_in_its_own_span(exporter) -> None:
    embedded = pipeline._embed(FakeEmbeddings(768), ["a", "b", "c", "d", "e"], batch_size=2)

    batches = _by_name(exporter)["embeddings fake-hash-v1"]
    assert embedded.calls == 3
    assert len(batches) == 3
    assert {b.attributes["gen_ai.operation.name"] for b in batches} == {"embeddings"}


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gemini/gemini-3.5-flash", ModelRef("gcp.gemini", "gemini-3.5-flash")),
        ("gemini-embedding-001@768", ModelRef("gcp.gemini", "gemini-embedding-001", 768)),
        ("bge-base-en-v1.5@768", ModelRef("rutaviva.local", "bge-base-en-v1.5", 768)),
        ("fake-extractive-v3", ModelRef("rutaviva.fake", "fake-extractive-v3")),
        ("fake-hash-v1@768", ModelRef("rutaviva.fake", "fake-hash-v1", 768)),
    ],
)
def test_model_ids_map_to_provider_and_model(model_id: str, expected: ModelRef) -> None:
    assert model_ref(model_id) == expected


class JSONClient:
    model_id = "gemini/fast"

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.last_usage = (50, 4)

    def complete_json(self, system: str, user: str, *, timeout_s: float) -> dict:
        return self.reply


def test_judge_and_resolver_calls_are_chat_spans(exporter) -> None:
    from app.conversations.resolver import LLMQuestionResolver, Turn
    from app.retrieval.judge import LLMRelevanceJudge

    LLMRelevanceJudge(JSONClient({"relevant_ids": [str(CHUNK.chunk_id)]})).judge(QUESTION, [CHUNK])
    LLMQuestionResolver(
        JSONClient({"question": "What is the cap on the penalty?", "uses_context": True})
    ).resolve("And the cap?", [Turn(question=QUESTION, answer="1.5% per day.")])

    chats = _by_name(exporter)["chat fast"]
    assert [c.attributes["gen_ai.prompt.name"] for c in chats] == [
        "relevance_judge",
        "resolve_follow_up",
    ]
    for chat in chats:
        assert chat.kind == SpanKind.CLIENT
        assert chat.attributes["gen_ai.provider.name"] == "gcp.gemini"
        assert chat.attributes["gen_ai.usage.input_tokens"] == 50
    # The judge's call is a child of its `retrieval.judge` step.
    [judge_step] = _by_name(exporter)["retrieval.judge"]
    assert chats[0].parent.span_id == judge_step.context.span_id

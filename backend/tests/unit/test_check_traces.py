"""`scripts/check_traces.py` (run by the CI e2e job against Jaeger) on Jaeger-shaped
traces built here, so its rules are checked without a stack."""

from typing import Any

from scripts import check_traces

INGEST_TRACE = "aaa"
QUERY_TRACE = "bbb"


def _span(name: str, **tags: Any) -> dict[str, Any]:
    return {
        "operationName": name,
        "tags": [{"key": key, "value": value} for key, value in tags.items()],
        "references": [],
    }


def _query_trace() -> dict[str, Any]:
    workflow = _span(
        "invoke_workflow contract_qa",
        **{"gen_ai.operation.name": "invoke_workflow", "gen_ai.workflow.name": "contract_qa"},
    )
    workflow["references"] = [{"refType": "FOLLOWS_FROM", "traceID": INGEST_TRACE}]
    chat = _span(
        "chat fake-extractive-v3",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "rutaviva.fake",
            "gen_ai.request.model": "fake-extractive-v3",
            "gen_ai.prompt.name": "answer",
            "llm.latency_ms": 12,
            "gen_ai.usage.input_tokens": 300,
            "gen_ai.usage.output_tokens": 40,
        },
    )
    return {
        "traceID": QUERY_TRACE,
        "processes": {"p1": {"serviceName": "rutaviva-api"}},
        "spans": [
            workflow,
            _span("embeddings bge-base-en-v1.5", **{"gen_ai.operation.name": "embeddings"}),
            _span("retrieval", **{"gen_ai.operation.name": "retrieval"}),
            chat,
        ],
    }


def _ingest_trace() -> dict[str, Any]:
    return {
        "traceID": INGEST_TRACE,
        "processes": {
            "p1": {"serviceName": "rutaviva-api"},
            "p2": {"serviceName": "rutaviva-worker"},
        },
        "spans": [
            _span("POST /documents"),
            _span("ingest.extract"),
            _span("ingest.chunk"),
            _span("ingest.embed"),
            _span("embeddings bge-base-en-v1.5", **{"gen_ai.operation.name": "embeddings"}),
            _span("ingest.store"),
        ],
    }


def test_conforming_traces_pass() -> None:
    assert check_traces.check_query(_query_trace()) == []
    assert check_traces.check_ingestion(_ingest_trace()) == []


def test_old_span_names_fail() -> None:
    trace = _query_trace()
    trace["spans"][0]["operationName"] = "query.answer"

    assert check_traces.check_query(trace) == [
        "query trace has neither 'invoke_agent contract_analyst' nor 'invoke_workflow contract_qa'"
    ]


def _agent_trace(with_tool: bool = True) -> dict[str, Any]:
    trace = _query_trace()
    root = trace["spans"][0]
    root["operationName"] = "invoke_agent contract_analyst"
    root["tags"] = [
        {"key": "gen_ai.operation.name", "value": "invoke_agent"},
        {"key": "gen_ai.agent.name", "value": "contract_analyst"},
    ]
    if with_tool:
        trace["spans"].append(
            _span(
                "execute_tool search_contracts",
                **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_contracts"},
            )
        )
    return trace


def test_an_agent_trace_passes_with_its_tool_spans() -> None:
    assert check_traces.check_query(_agent_trace()) == []
    assert check_traces.check_query(_agent_trace(with_tool=False)) == [
        "agent trace has no `execute_tool` span"
    ]


def test_answer_chat_span_needs_its_attributes_and_name() -> None:
    trace = _query_trace()
    chat = trace["spans"][-1]
    chat["operationName"] = "llm.generate"
    chat["tags"] = [t for t in chat["tags"] if t["key"] != "gen_ai.provider.name"]

    problems = check_traces.check_query(trace)

    assert "llm.generate lacks ['gen_ai.provider.name']" in problems
    assert "chat span is named 'llm.generate', not 'chat {model}'" in problems


def test_missing_answer_chat_span_or_link_fails() -> None:
    trace = _query_trace()
    trace["spans"][-1]["tags"][3]["value"] = "relevance_judge"  # only a judge call
    trace["spans"][0]["references"] = []

    assert check_traces.check_query(trace) == [
        "query trace has no `chat` span for the answer prompt"
    ]
    trace = _query_trace()
    trace["spans"][0]["references"] = []
    assert check_traces.check_query(trace) == [
        "invoke_workflow contract_qa has no link to an ingestion trace"
    ]


def test_ingestion_needs_embedding_calls() -> None:
    trace = _ingest_trace()
    trace["spans"] = [s for s in trace["spans"] if not s["operationName"].startswith("embeddings")]

    assert check_traces.check_ingestion(trace) == ["upload trace has no GenAI `embeddings` span"]

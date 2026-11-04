"""Check, through Jaeger's query API, that the traces the brief asks for exist.

Run after documents were uploaded and questions asked (CI runs it after `make eval`):

1. One upload trace crosses the API and the worker: `POST /documents` in `rutaviva-api`
   and the ingestion spans (extract, chunk, embed, store) in `rutaviva-worker`, with the
   embedding calls as GenAI `embeddings {model}` spans.
2. A question's trace follows the GenAI semantic conventions (ADR-018): the workflow
   `invoke_workflow contract_qa`, `embeddings {model}`, `retrieval`, and the answer's
   `chat {model}` CLIENT span with provider, model, latency and tokens. The workflow span
   links to the ingestion trace of a document it used.

Standard library only, so it runs on the CI runner without the backend environment.

Usage: python3 backend/scripts/check_traces.py [--jaeger http://localhost:16686]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

INGESTION_SPANS = {"ingest.extract", "ingest.chunk", "ingest.embed", "ingest.store"}
WORKFLOW_SPAN = "invoke_workflow contract_qa"
QUERY_SPANS = {WORKFLOW_SPAN, "retrieval"}
CHAT_ATTRIBUTES = {
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.prompt.name",
    "llm.latency_ms",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
}


def find_trace(jaeger: str, service: str, operation: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(
        {"service": service, "operation": operation, "limit": 1, "lookback": "2h"}
    )
    with urllib.request.urlopen(f"{jaeger}/api/traces?{query}", timeout=10) as response:
        data = json.load(response).get("data") or []
    return data[0] if data else None


def services(trace: dict[str, Any]) -> set[str]:
    return {process["serviceName"] for process in trace["processes"].values()}


def operations(trace: dict[str, Any]) -> set[str]:
    return {span["operationName"] for span in trace["spans"]}


def span_named(trace: dict[str, Any], name: str) -> dict[str, Any]:
    return next(span for span in trace["spans"] if span["operationName"] == name)


def tags(span: dict[str, Any]) -> dict[str, Any]:
    return {tag["key"]: tag["value"] for tag in span["tags"]}


def genai_spans(trace: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    return [s for s in trace["spans"] if tags(s).get("gen_ai.operation.name") == operation]


def check_ingestion(trace: dict[str, Any]) -> list[str]:
    problems = []
    if not {"rutaviva-api", "rutaviva-worker"} <= services(trace):
        problems.append(f"upload trace is not shared by API and worker: {sorted(services(trace))}")
    if missing := INGESTION_SPANS - operations(trace):
        problems.append(f"upload trace lacks {sorted(missing)}")
    if not genai_spans(trace, "embeddings"):
        problems.append("upload trace has no GenAI `embeddings` span")
    return problems


def check_query(trace: dict[str, Any]) -> list[str]:
    if missing := QUERY_SPANS - operations(trace):
        return [f"query trace lacks {sorted(missing)}"]
    problems = []
    if not genai_spans(trace, "embeddings"):
        problems.append("query trace has no GenAI `embeddings` span")
    answers = [
        s for s in genai_spans(trace, "chat") if tags(s).get("gen_ai.prompt.name") == "answer"
    ]
    if not answers:
        return [*problems, "query trace has no `chat` span for the answer prompt"]
    chat = answers[-1]
    chat_tags = tags(chat)
    if missing := CHAT_ATTRIBUTES - set(chat_tags):
        problems.append(f"{chat['operationName']} lacks {sorted(missing)}")
    if chat["operationName"] != f"chat {chat_tags.get('gen_ai.request.model')}":
        problems.append(f"chat span is named {chat['operationName']!r}, not 'chat {{model}}'")
    workflow = span_named(trace, WORKFLOW_SPAN)
    links = [
        ref
        for ref in workflow["references"]
        if ref["refType"] == "FOLLOWS_FROM" and ref["traceID"] != trace["traceID"]
    ]
    if not links:
        problems.append(f"{WORKFLOW_SPAN} has no link to an ingestion trace")
    return problems


def wait_for(jaeger: str, service: str, operation: str, timeout_s: float) -> dict[str, Any]:
    """Spans are exported in batches: give the collector a few seconds."""
    deadline = time.monotonic() + timeout_s
    while True:
        trace = find_trace(jaeger, service, operation)
        if trace is not None or time.monotonic() > deadline:
            if trace is None:
                raise SystemExit(f"FAIL no trace for {service} / {operation}")
            return trace
        time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jaeger", default="http://localhost:16686")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    ingestion = wait_for(args.jaeger, "rutaviva-worker", "ingest.embed", args.timeout)
    query = wait_for(args.jaeger, "rutaviva-api", WORKFLOW_SPAN, args.timeout)
    problems = check_ingestion(ingestion) + check_query(query)

    print(f"upload trace {ingestion['traceID']}: {sorted(services(ingestion))}")
    print(f"query trace  {query['traceID']}: {len(query['spans'])} spans")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print(
            "PASS traces: upload crosses API and worker; query follows the GenAI "
            "conventions and links to its ingestion"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

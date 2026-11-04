"""Check, through Jaeger's query API, that the traces the brief asks for exist.

Run after documents were uploaded and questions asked (CI runs it after `make eval`):

1. One upload trace crosses the API and the worker: `POST /documents` in `rutaviva-api`
   and the ingestion spans (extract, chunk, embed, store) in `rutaviva-worker`, with the
   embedding calls as GenAI `embeddings {model}` spans.
2. A question's trace follows the GenAI semantic conventions (ADR-018): its root is the
   agent `invoke_agent contract_analyst` (with `execute_tool` spans, ADR-021) or the
   workflow `invoke_workflow contract_qa`; then `embeddings {model}`, `retrieval`, and
   the answer's `chat {model}` span with provider, model, latency and tokens. The root
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
# The root of a question: the agent (QUERY_MODE=auto, ADR-021) or the fixed workflow.
AGENT_SPAN = "invoke_agent contract_analyst"
WORKFLOW_SPAN = "invoke_workflow contract_qa"
ROOT_SPANS = (AGENT_SPAN, WORKFLOW_SPAN)
# The prompts that produce the answer: the workflow's (also the keyless agent's) and
# the model agent's.
ANSWER_PROMPTS = {"answer", "contract_analyst"}
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
    roots = [name for name in ROOT_SPANS if name in operations(trace)]
    if not roots:
        return [f"query trace has neither {AGENT_SPAN!r} nor {WORKFLOW_SPAN!r}"]
    root = roots[0]
    if "retrieval" not in operations(trace):
        return ["query trace lacks ['retrieval']"]
    problems = []
    if root == AGENT_SPAN and not genai_spans(trace, "execute_tool"):
        problems.append("agent trace has no `execute_tool` span")
    if not genai_spans(trace, "embeddings"):
        problems.append("query trace has no GenAI `embeddings` span")
    answers = [
        s for s in genai_spans(trace, "chat") if tags(s).get("gen_ai.prompt.name") in ANSWER_PROMPTS
    ]
    if not answers:
        return [*problems, "query trace has no `chat` span for the answer prompt"]
    chat = answers[-1]
    chat_tags = tags(chat)
    if missing := CHAT_ATTRIBUTES - set(chat_tags):
        problems.append(f"{chat['operationName']} lacks {sorted(missing)}")
    if chat["operationName"] != f"chat {chat_tags.get('gen_ai.request.model')}":
        problems.append(f"chat span is named {chat['operationName']!r}, not 'chat {{model}}'")
    links = [
        ref
        for ref in span_named(trace, root)["references"]
        if ref["refType"] == "FOLLOWS_FROM" and ref["traceID"] != trace["traceID"]
    ]
    if not links:
        problems.append(f"{root} has no link to an ingestion trace")
    return problems


def wait_for(
    jaeger: str, service: str, operations_: tuple[str, ...], timeout_s: float
) -> dict[str, Any]:
    """The newest trace with any of `operations_`. Spans are exported in batches: give
    the collector a few seconds."""
    deadline = time.monotonic() + timeout_s
    while True:
        for operation in operations_:
            trace = find_trace(jaeger, service, operation)
            if trace is not None:
                return trace
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL no trace for {service} / {' or '.join(operations_)}")
        time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jaeger", default="http://localhost:16686")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    ingestion = wait_for(args.jaeger, "rutaviva-worker", ("ingest.embed",), args.timeout)
    query = wait_for(args.jaeger, "rutaviva-api", ROOT_SPANS, args.timeout)
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

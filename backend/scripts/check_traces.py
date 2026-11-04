"""Check, through Jaeger's query API, that the traces the brief asks for exist.

Run after documents were uploaded and questions asked (CI runs it after `make eval`):

1. One upload trace crosses the API and the worker: `POST /documents` in `rutaviva-api`
   and the ingestion spans (extract, chunk, embed, store) in `rutaviva-worker`.
2. A question's trace has the retrieval, the LLM call (with its latency and tokens) and a
   link from `query.answer` to the ingestion trace of a document it used.

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
QUERY_SPANS = {"query.retrieve", "llm.generate", "query.answer"}
LLM_ATTRIBUTES = {"llm.latency_ms", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}


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


def check_ingestion(trace: dict[str, Any]) -> list[str]:
    problems = []
    if not {"rutaviva-api", "rutaviva-worker"} <= services(trace):
        problems.append(f"upload trace is not shared by API and worker: {sorted(services(trace))}")
    if missing := INGESTION_SPANS - operations(trace):
        problems.append(f"upload trace lacks {sorted(missing)}")
    return problems


def check_query(trace: dict[str, Any]) -> list[str]:
    if missing := QUERY_SPANS - operations(trace):
        return [f"query trace lacks {sorted(missing)}"]
    problems = []
    llm_tags = {tag["key"] for tag in span_named(trace, "llm.generate")["tags"]}
    if missing := LLM_ATTRIBUTES - llm_tags:
        problems.append(f"llm.generate lacks {sorted(missing)}")
    answer = span_named(trace, "query.answer")
    links = [
        ref
        for ref in answer["references"]
        if ref["refType"] == "FOLLOWS_FROM" and ref["traceID"] != trace["traceID"]
    ]
    if not links:
        problems.append("query.answer has no link to an ingestion trace")
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
    query = wait_for(args.jaeger, "rutaviva-api", "query.answer", args.timeout)
    problems = check_ingestion(ingestion) + check_query(query)

    print(f"upload trace {ingestion['traceID']}: {sorted(services(ingestion))}")
    print(f"query trace  {query['traceID']}: {len(query['spans'])} spans")
    for problem in problems:
        print(f"FAIL {problem}")
    if not problems:
        print("PASS traces: upload crosses API and worker; query links to its ingestion")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

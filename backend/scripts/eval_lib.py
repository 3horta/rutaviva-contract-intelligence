"""Checking logic of the evaluation harness (`scripts/eval.py`).

The harness checks BEHAVIOUR, not scores: for each question of the evaluation set it
asks the running API and checks what an analyst would check by hand. Was it answered or
refused? Is the expected page cited? Does the answer contain the key figure and avoid the
forbidden ones? Did every cited or retrieved chunk come from the asking tenant? Is the
confidence capped where the documents contradict each other? Was the injection flagged?

Everything here is pure: the HTTP calls go through the small `Api` protocol, so the unit
tests run every check with a fake API and no network (`tests/unit/test_eval_lib.py`).
"""

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml

OUTCOMES = ("answered", "insufficient_evidence")
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
EXPECT_KEYS = {
    "outcome",
    "sources",
    "answer_has_any",
    "answer_lacks",
    "results_in",
    "max_confidence",
    "warnings_has",
}
FINAL_DOCUMENT_STATUSES = ("ready", "failed")

PASS, FAIL, WARN, FLAKY = "PASS", "FAIL", "WARN", "FLAKY"


# --- The evaluation set -------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    document: str  # filename, as the API reports it
    page: int


@dataclass(frozen=True)
class Expect:
    outcome: str
    sources: tuple[SourceRef, ...] = ()
    answer_has_any: tuple[str, ...] = ()
    answer_lacks: tuple[str, ...] = ()
    results_in_tenant: str | None = None
    max_confidence: str | None = None
    warnings_has: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    id: str
    tenant: str
    question: str
    kind: str
    soft: bool
    expect: Expect
    note: str = ""


class EvalSetError(ValueError):
    """The questions file does not follow the schema."""


def load_cases(path: Path) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise EvalSetError(f"{path}: expected a mapping with a `questions` list")
    cases = [parse_case(raw) for raw in data["questions"]]
    ids = Counter(case.id for case in cases)
    duplicated = sorted(case_id for case_id, count in ids.items() if count > 1)
    if duplicated:
        raise EvalSetError(f"{path}: duplicated ids: {', '.join(duplicated)}")
    return cases


def parse_case(raw: dict[str, Any]) -> Case:
    case_id = raw.get("id", "?")
    expect = raw.get("expect") or {}
    # Strict on purpose: a typo in a key would silently turn a check off.
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise EvalSetError(f"{case_id}: unknown expect keys: {', '.join(sorted(unknown))}")
    outcome = expect.get("outcome")
    if outcome not in OUTCOMES:
        raise EvalSetError(f"{case_id}: expect.outcome must be one of {OUTCOMES}")
    max_confidence = expect.get("max_confidence")
    if max_confidence is not None and max_confidence not in CONFIDENCE_ORDER:
        raise EvalSetError(f"{case_id}: unknown max_confidence {max_confidence!r}")
    try:
        return Case(
            id=str(raw["id"]),
            tenant=str(raw["tenant"]),
            question=str(raw["question"]),
            kind=str(raw["kind"]),
            soft=bool(raw.get("soft", False)),
            note=str(raw.get("note", "")),
            expect=Expect(
                outcome=outcome,
                sources=tuple(
                    SourceRef(document=str(s["document"]), page=int(s["page"]))
                    for s in expect.get("sources") or []
                ),
                answer_has_any=tuple(expect.get("answer_has_any") or []),
                answer_lacks=tuple(expect.get("answer_lacks") or []),
                results_in_tenant=(expect.get("results_in") or {}).get("tenant"),
                max_confidence=max_confidence,
                warnings_has=tuple(expect.get("warnings_has") or []),
            ),
        )
    except KeyError as exc:
        raise EvalSetError(f"{case_id}: missing field {exc}") from None


def select_cases(
    cases: list[Case], only: Iterable[str] = (), kinds: Iterable[str] = ()
) -> list[Case]:
    only, kinds = set(only), set(kinds)
    unknown = only - {case.id for case in cases}
    if unknown:
        raise EvalSetError(f"unknown ids in --only: {', '.join(sorted(unknown))}")
    return [
        case
        for case in cases
        if (not only or case.id in only) and (not kinds or case.kind in kinds)
    ]


# --- Checks of one answer ----------------------------------------------------------------
# Each check returns None when it passes, or a one-line reason when it fails.


def check_outcome(expect: Expect, response: dict[str, Any]) -> str | None:
    status = response.get("status")
    if status != expect.outcome:
        return f"outcome: expected {expect.outcome}, got {status}"
    return None


def check_sources(expect: Expect, response: dict[str, Any]) -> str | None:
    """At least one citation must match one expected (filename, page)."""
    if not expect.sources:
        return None
    expected = {(s.document, s.page) for s in expect.sources}
    cited = {(c["document_filename"], c["page"]) for c in response.get("citations", [])}
    if expected & cited:
        return None
    wanted = ", ".join(f"{d} p.{p}" for d, p in sorted(expected))
    got = ", ".join(f"{d} p.{p}" for d, p in sorted(cited)) or "no citations"
    return f"sources: expected one of [{wanted}], cited [{got}]"


def check_answer_has_any(expect: Expect, response: dict[str, Any]) -> str | None:
    if not expect.answer_has_any:
        return None
    answer = response.get("answer", "").lower()
    if any(needle.lower() in answer for needle in expect.answer_has_any):
        return None
    return f"answer_has_any: none of {list(expect.answer_has_any)} in the answer"


def check_answer_lacks(expect: Expect, response: dict[str, Any]) -> str | None:
    answer = response.get("answer", "").lower()
    found = [needle for needle in expect.answer_lacks if needle.lower() in answer]
    if found:
        return f"answer_lacks: the answer contains {found}"
    return None


def check_results_in_tenant(response: dict[str, Any], own_document_ids: set[str]) -> str | None:
    """Cross-tenant leak check: every cited and every retrieved chunk must come from a
    document of the asking tenant (the ids it sees in its own `GET /documents`)."""
    referenced = [c["document_id"] for c in response.get("citations", [])]
    details = response.get("details") or {}
    referenced += [r["document_id"] for r in details.get("retrieved", [])]
    foreign = sorted({str(doc_id) for doc_id in referenced} - own_document_ids)
    if foreign:
        return f"LEAK: {len(foreign)} document(s) outside the tenant: {', '.join(foreign)}"
    return None


def check_max_confidence(expect: Expect, response: dict[str, Any]) -> str | None:
    if expect.max_confidence is None:
        return None
    confidence = response.get("confidence")
    if CONFIDENCE_ORDER.get(confidence, 99) > CONFIDENCE_ORDER[expect.max_confidence]:
        return f"max_confidence: expected at most {expect.max_confidence}, got {confidence}"
    return None


def check_warnings_has(expect: Expect, response: dict[str, Any]) -> str | None:
    missing = [w for w in expect.warnings_has if w not in response.get("warnings", [])]
    if missing:
        return f"warnings_has: missing {missing} (got {response.get('warnings', [])})"
    return None


# --- Global checks, run on every case ------------------------------------------------------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_INTERNALS = [
    ("a chunk/document UUID", _UUID),
    ("a Python traceback", re.compile(r"\bTraceback\b")),
    ("SQL", re.compile(r"\bSELECT\b[\s\S]+?\bFROM\b|\bINSERT\s+INTO\b|\bpsycopg\b", re.I)),
    ("a <document> context tag", re.compile(r"</?document\b", re.I)),
]
# A number with optional thousands/decimal separators: 180,000 · 1.5 · 1,5 · 0.02 · 4/32.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def check_non_empty_answer(response: dict[str, Any]) -> str | None:
    if not str(response.get("answer", "")).strip():
        return "empty answer"
    return None


def check_no_internals(response: dict[str, Any]) -> str | None:
    answer = str(response.get("answer", ""))
    leaked = [label for label, pattern in _INTERNALS if pattern.search(answer)]
    if leaked:
        return f"internals in the answer: {', '.join(leaked)}"
    return None


def normalize_number(raw: str) -> str:
    """Canonical form of a number as written in English or Spanish contracts.

    - Both `,` and `.`: the last one is the decimal separator ("1,234.5" / "1.234,5").
    - One kind, several times: thousands ("1,000,000").
    - One kind, once: thousands if exactly three digits follow ("180,000"), else decimal
      ("1.5", "1,5", "0.02").
    Trailing zeros are dropped, so "75.00" == "75" and "0.50" == "0.5".
    """
    if "," in raw and "." in raw:
        decimal_sep = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
    else:
        sep = "," if "," in raw else "." if "." in raw else None
        if sep is not None:
            if raw.count(sep) > 1 or len(raw.split(sep)[1]) == 3:
                raw = raw.replace(sep, "")
            else:
                raw = raw.replace(sep, ".")
    value = Decimal(raw).normalize()
    return format(value, "f")


def numbers_in(text: str) -> set[str]:
    return {normalize_number(match) for match in _NUMBER.findall(text)}


def check_numbers_grounded(question: str, response: dict[str, Any]) -> str | None:
    """Cheap hallucination check: every number in an answer must appear in the question,
    in one of the cited quotes (which the API already checked against the chunks) or be
    the page of a citation (answers say "page 2" when they point to the source)."""
    if response.get("status") != "answered":
        return None
    allowed = numbers_in(question)
    for citation in response.get("citations", []):
        allowed |= numbers_in(citation.get("quote", ""))
        if citation.get("page") is not None:
            allowed.add(normalize_number(str(citation["page"])))
    ungrounded = sorted(numbers_in(str(response.get("answer", ""))) - allowed)
    if ungrounded:
        return f"numbers not in the question or the quotes: {ungrounded}"
    return None


def check_response(case: Case, response: dict[str, Any], own_document_ids: set[str]) -> list[str]:
    """Every failed check of one answer, as one-line reasons (empty list = pass)."""
    expect = case.expect
    results = [
        check_non_empty_answer(response),
        check_no_internals(response),
        check_outcome(expect, response),
        check_sources(expect, response),
        check_answer_has_any(expect, response),
        check_answer_lacks(expect, response),
        check_results_in_tenant(response, own_document_ids),
        check_max_confidence(expect, response),
        check_warnings_has(expect, response),
        check_numbers_grounded(case.question, response),
    ]
    return [reason for reason in results if reason is not None]


# --- Running the cases ---------------------------------------------------------------------


class ApiError(RuntimeError):
    """A non-2xx answer from the API (or no answer at all)."""


class Api(Protocol):
    """What the harness needs from the API. `scripts/eval.py` implements it with httpx;
    the tests with a dict."""

    def query(self, token: str, question: str) -> dict[str, Any]: ...

    def list_documents(self, token: str) -> list[dict[str, Any]]: ...

    def upload(self, token: str, path: Path) -> dict[str, Any]: ...

    def get_document(self, token: str, document_id: str) -> dict[str, Any]: ...


@dataclass
class RunResult:
    failures: list[str]
    latency_ms: int | None = None


@dataclass
class CaseResult:
    case: Case
    runs: list[RunResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        """PASS if every run passed. A soft case that failed is a WARN. Mixed results
        over `--repeat` runs are FLAKY: the behaviour is not stable."""
        passed = sum(1 for run in self.runs if not run.failures)
        if passed == len(self.runs):
            return PASS
        if passed > 0:
            return FLAKY
        return WARN if self.case.soft else FAIL

    @property
    def reason(self) -> str:
        failed = [run for run in self.runs if run.failures]
        if not failed:
            return ""
        first = failed[0].failures
        reason = first[0] + (f" (+{len(first) - 1} more)" if len(first) > 1 else "")
        if self.status == FLAKY:
            passed = len(self.runs) - len(failed)
            reason = f"passed {passed}/{len(self.runs)}; {reason}"
        return reason

    @property
    def is_blocking(self) -> bool:
        """A hard (non-soft) case that failed at least once makes the run fail: FLAKY
        counts, because a hard case must be both right and stable."""
        return not self.case.soft and self.status in (FAIL, FLAKY)


def run_cases(
    api: Api,
    cases: list[Case],
    tokens: dict[str, str],
    own_document_ids: dict[str, set[str]],
    repeat: int = 1,
    on_result: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Ask every case `repeat` times, each in a fresh conversation (no conversation_id),
    so earlier answers never influence a case."""
    results = []
    for case in cases:
        result = CaseResult(case)
        # Normally the asking tenant; a case could check against another tenant's set.
        checked_tenant = case.expect.results_in_tenant or case.tenant
        for _ in range(repeat):
            try:
                response = api.query(tokens[case.tenant], case.question)
            except ApiError as exc:
                result.runs.append(RunResult(failures=[f"API error: {exc}"]))
                continue
            failures = check_response(case, response, own_document_ids[checked_tenant])
            latency = (response.get("details") or {}).get("latency_ms")
            result.runs.append(RunResult(failures=failures, latency_ms=latency))
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def exit_code(results: list[CaseResult]) -> int:
    return 1 if any(result.is_blocking for result in results) else 0


# --- Documents setup -----------------------------------------------------------------------


def upload_and_wait(
    api: Api,
    token: str,
    pdfs: list[Path],
    timeout_s: float,
    poll_s: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, dict[str, Any]]:
    """Upload the PDFs (a duplicate returns the existing document) and wait until each one
    is `ready` or `failed`. Returns {filename: last document state}; a document still
    processing at the timeout keeps its last status."""
    ids = {pdf.name: str(api.upload(token, pdf)["document_id"]) for pdf in pdfs}
    states: dict[str, dict[str, Any]] = {}
    deadline = clock() + timeout_s
    pending = dict(ids)
    while True:
        for name, document_id in list(pending.items()):
            states[name] = api.get_document(token, document_id)
            if states[name]["status"] in FINAL_DOCUMENT_STATUSES:
                del pending[name]
        if not pending or clock() >= deadline:
            return states
        sleep(poll_s)


# --- Report --------------------------------------------------------------------------------


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile (no interpolation): a value that was actually observed."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def format_report(results: list[CaseResult]) -> str:
    lines = []
    id_width = max([len(r.case.id) for r in results] + [2])
    kind_width = max([len(r.case.kind) for r in results] + [4])
    lines.append(f"{'id':<{id_width}}  {'kind':<{kind_width}}  {'status':<6}  reason")
    lines.append("-" * (id_width + kind_width + 24))
    for r in results:
        lines.append(
            f"{r.case.id:<{id_width}}  {r.case.kind:<{kind_width}}  {r.status:<6}  {r.reason}"
        )

    lines.append("")
    lines.append("By kind:")
    kinds = sorted({r.case.kind for r in results})
    for kind in kinds:
        counts = Counter(r.status for r in results if r.case.kind == kind)
        summary = "  ".join(f"{s} {counts[s]}" for s in (PASS, FAIL, WARN, FLAKY) if counts[s])
        lines.append(f"  {kind:<{kind_width}}  {summary}")
    totals = Counter(r.status for r in results)
    total_line = "  ".join(f"{s} {totals[s]}" for s in (PASS, FAIL, WARN, FLAKY))
    lines.append(f"  {'total':<{kind_width}}  {total_line}")

    latencies = [run.latency_ms for r in results for run in r.runs if run.latency_ms is not None]
    if latencies:
        lines.append("")
        lines.append(
            f"Latency (details.latency_ms, {len(latencies)} answers): "
            f"p50 {percentile(latencies, 50)} ms · p95 {percentile(latencies, 95)} ms · "
            f"mean {round(statistics.mean(latencies))} ms"
        )
    return "\n".join(lines)


def results_as_json(results: list[CaseResult]) -> dict[str, Any]:
    latencies = [run.latency_ms for r in results for run in r.runs if run.latency_ms is not None]
    return {
        "summary": dict(Counter(r.status for r in results)),
        "latency_ms": (
            {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)}
            if latencies
            else None
        ),
        "exit_code": exit_code(results),
        "cases": [
            {
                "id": r.case.id,
                "kind": r.case.kind,
                "tenant": r.case.tenant,
                "soft": r.case.soft,
                "status": r.status,
                "reason": r.reason,
                "runs": [
                    {"failures": run.failures, "latency_ms": run.latency_ms} for run in r.runs
                ],
            }
            for r in results
        ],
    }

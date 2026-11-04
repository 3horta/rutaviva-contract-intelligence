"""The checking logic of the evaluation harness, with a fake API (no network)."""

from pathlib import Path
from typing import Any

import pytest

from scripts import eval_lib
from scripts.eval_lib import (
    FAIL,
    FLAKY,
    PASS,
    WARN,
    ApiError,
    Case,
    CaseResult,
    EvalSetError,
    Expect,
    RunResult,
    SourceRef,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OWN = "11111111-1111-1111-1111-111111111111"
FOREIGN = "22222222-2222-2222-2222-222222222222"


def make_case(soft: bool = False, **expect: Any) -> Case:
    return Case(
        id="c1",
        tenant="tenant_a",
        question="What is the detention rate?",
        kind="lexical",
        soft=soft,
        expect=Expect(**{"outcome": "answered", **expect}),
    )


def make_response(**overrides: Any) -> dict[str, Any]:
    response = {
        "answer": "Detention is billed at $75.00 per hour.",
        "status": "answered",
        "confidence": "high",
        "citations": [
            {
                "chunk_id": "c0000000-0000-0000-0000-000000000000",
                "document_id": OWN,
                "document_filename": "carrier.pdf",
                "page": 1,
                "quote": "detention at $75.00 per hour after 2 hours",
            }
        ],
        "warnings": [],
        "details": {"retrieved": [{"document_id": OWN}], "latency_ms": 120},
    }
    response.update(overrides)
    return response


# --- Expectation checks -------------------------------------------------------------------


def test_a_good_answer_passes_every_check():
    case = make_case(
        sources=(SourceRef("carrier.pdf", 1),),
        answer_has_any=("$75",),
        answer_lacks=("Rio Bravo",),
        max_confidence="high",
    )
    assert eval_lib.check_response(case, make_response(), {OWN}) == []


def test_outcome_mismatch_fails():
    reason = eval_lib.check_outcome(
        Expect(outcome="insufficient_evidence"), make_response(status="answered")
    )
    assert reason == "outcome: expected insufficient_evidence, got answered"


def test_sources_needs_one_citation_with_the_expected_file_and_page():
    expect = Expect(
        outcome="answered", sources=(SourceRef("carrier.pdf", 2), SourceRef("carrier.pdf", 1))
    )
    assert eval_lib.check_sources(expect, make_response()) is None
    wrong_page = Expect(outcome="answered", sources=(SourceRef("carrier.pdf", 3),))
    assert "cited [carrier.pdf p.1]" in eval_lib.check_sources(wrong_page, make_response())
    assert "no citations" in eval_lib.check_sources(wrong_page, make_response(citations=[]))


def test_answer_has_any_is_case_insensitive_and_needs_one_match():
    expect = Expect(outcome="answered", answer_has_any=("nothing", "PER HOUR"))
    assert eval_lib.check_answer_has_any(expect, make_response()) is None
    expect = Expect(outcome="answered", answer_has_any=("$90",))
    assert eval_lib.check_answer_has_any(expect, make_response()).startswith("answer_has_any")


def test_answer_lacks_fails_on_any_forbidden_string():
    expect = Expect(outcome="answered", answer_lacks=("halcon", "detention is billed"))
    assert eval_lib.check_answer_lacks(expect, make_response()) == (
        "answer_lacks: the answer contains ['detention is billed']"
    )
    assert eval_lib.check_answer_lacks(Expect(outcome="answered"), make_response()) is None


def test_max_confidence_is_a_cap():
    cap = Expect(outcome="answered", max_confidence="medium")
    assert eval_lib.check_max_confidence(cap, make_response(confidence="low")) is None
    assert eval_lib.check_max_confidence(cap, make_response(confidence="medium")) is None
    assert "got high" in eval_lib.check_max_confidence(cap, make_response(confidence="high"))


def test_warnings_has_needs_every_warning():
    expect = Expect(outcome="answered", warnings_has=("prompt_injection",))
    assert eval_lib.check_warnings_has(expect, make_response(warnings=["prompt_injection"])) is None
    assert "missing ['prompt_injection']" in eval_lib.check_warnings_has(expect, make_response())


# --- Leak check --------------------------------------------------------------------------


def test_no_leak_when_every_document_is_the_tenants():
    assert eval_lib.check_results_in_tenant(make_response(), {OWN}) is None


def test_a_foreign_citation_is_a_leak():
    citation = {**make_response()["citations"][0], "document_id": FOREIGN}
    reason = eval_lib.check_results_in_tenant(make_response(citations=[citation]), {OWN})
    assert reason.startswith("LEAK") and FOREIGN in reason


def test_a_foreign_retrieved_chunk_is_a_leak_even_if_not_cited():
    details = {"retrieved": [{"document_id": OWN}, {"document_id": FOREIGN}], "latency_ms": 1}
    reason = eval_lib.check_results_in_tenant(make_response(details=details), {OWN})
    assert reason.startswith("LEAK")


# --- Global checks -----------------------------------------------------------------------


def test_empty_answer_fails():
    assert eval_lib.check_non_empty_answer(make_response(answer="  ")) == "empty answer"


@pytest.mark.parametrize(
    "answer",
    [
        "See chunk 3f2b8c1e-1234-4abc-9def-0123456789ab.",
        "Traceback (most recent call last): ...",
        "SELECT content FROM chunks WHERE tenant_id = 1",
        'Per <document id="1"> the rate is high.',
    ],
)
def test_internals_in_the_answer_fail(answer):
    assert eval_lib.check_no_internals(make_response(answer=answer)).startswith("internals")


def test_a_plain_answer_has_no_internals():
    assert eval_lib.check_no_internals(make_response()) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("180,000", "180000"),
        ("180.000", "180000"),
        ("1,000,000", "1000000"),
        ("1.5", "1.5"),
        ("1,5", "1.5"),
        ("1,234.50", "1234.5"),
        ("1.234,50", "1234.5"),
        ("75.00", "75"),
        ("0.50", "0.5"),
        ("03", "3"),
        ("100", "100"),
    ],
)
def test_number_normalisation(raw, expected):
    assert eval_lib.normalize_number(raw) == expected


def test_numbers_are_extracted_from_text():
    assert eval_lib.numbers_in("1.5% per day, cap 15%; $0.02 on 180,000 gallons; 4/32") == {
        "1.5",
        "15",
        "0.02",
        "180000",
        "4",
        "32",
    }


def test_numbers_grounded_in_quotes_or_question():
    # "75" comes from the quote (written 75.00), "2" from the quote too.
    response = make_response(answer="It is $75 per hour after 2 hours.")
    assert eval_lib.check_numbers_grounded("What is the detention rate?", response) is None


def test_a_number_written_differently_is_still_grounded():
    citation = {**make_response()["citations"][0], "quote": "a fee of 1,5% per day"}
    response = make_response(answer="The fee is 1.5%.", citations=[citation])
    assert eval_lib.check_numbers_grounded("What fee?", response) is None


def test_a_number_from_nowhere_is_flagged():
    response = make_response(answer="It is $90 per hour after 2 hours.")
    reason = eval_lib.check_numbers_grounded("What is the rate?", response)
    assert reason == "numbers not in the question or the quotes: ['90']"


def test_numbers_are_not_checked_when_not_answered():
    response = make_response(status="insufficient_evidence", answer="Not 42.", citations=[])
    assert eval_lib.check_numbers_grounded("q", response) is None


# --- Status, repeat and exit code ----------------------------------------------------------


def result_with(case: Case, *failures: list[str]) -> CaseResult:
    return CaseResult(case, [RunResult(failures=list(f)) for f in failures])


def test_status_pass_fail_and_soft_warn():
    assert result_with(make_case(), []).status == PASS
    assert result_with(make_case(), ["outcome: x"]).status == FAIL
    assert result_with(make_case(soft=True), ["outcome: x"]).status == WARN


def test_mixed_runs_are_flaky_even_for_a_soft_case():
    flaky = result_with(make_case(), [], ["outcome: x"], [])
    assert flaky.status == FLAKY
    assert flaky.reason == "passed 2/3; outcome: x"
    assert result_with(make_case(soft=True), [], ["outcome: x"]).status == FLAKY


def test_reason_counts_the_other_failures():
    assert result_with(make_case(), ["a", "b", "c"]).reason == "a (+2 more)"


def test_exit_code_only_for_hard_failures_or_hard_flakiness():
    assert eval_lib.exit_code([result_with(make_case(), [])]) == 0
    assert eval_lib.exit_code([result_with(make_case(soft=True), ["x"])]) == 0
    assert eval_lib.exit_code([result_with(make_case(soft=True), [], ["x"])]) == 0
    assert eval_lib.exit_code([result_with(make_case(), ["x"])]) == 1
    assert eval_lib.exit_code([result_with(make_case(), [], ["x"])]) == 1


# --- Running with a fake API -----------------------------------------------------------------


class FakeApi:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses  # dicts, or ApiError to raise
        self.questions: list[tuple[str, str]] = []

    def query(self, token: str, question: str) -> dict[str, Any]:
        self.questions.append((token, question))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def list_documents(self, token):  # pragma: no cover - not used here
        return []

    def upload(self, token, path):  # pragma: no cover
        raise NotImplementedError

    def get_document(self, token, document_id):  # pragma: no cover
        raise NotImplementedError


def test_run_cases_repeats_with_the_tenants_token_and_collects_latency():
    case = make_case(answer_has_any=("$75",))
    bad = make_response(answer="It costs nothing.")
    api = FakeApi([make_response(), bad, ApiError("POST /query -> 503 down")])

    [result] = eval_lib.run_cases(api, [case], {"tenant_a": "tok-a"}, {"tenant_a": {OWN}}, 3)

    assert api.questions == [("tok-a", case.question)] * 3
    assert result.status == FLAKY
    assert result.runs[2].failures == ["API error: POST /query -> 503 down"]
    assert [run.latency_ms for run in result.runs] == [120, 120, None]


def test_run_cases_flags_a_leak_against_the_asking_tenants_documents():
    citation = {**make_response()["citations"][0], "document_id": FOREIGN}
    api = FakeApi([make_response(citations=[citation])])
    [result] = eval_lib.run_cases(api, [make_case()], {"tenant_a": "t"}, {"tenant_a": {OWN}})
    assert result.status == FAIL and result.reason.startswith("LEAK")


def test_report_has_the_table_the_kind_summary_and_latency():
    results = [result_with(make_case(), [])]
    results[0].runs[0].latency_ms = 100
    report = eval_lib.format_report(results)
    assert "c1" in report and "PASS" in report
    assert "lexical" in report.split("By kind:")[1]
    assert "p50 100 ms" in report


def test_percentile_is_nearest_rank():
    values = list(range(1, 21))  # 1..20
    assert eval_lib.percentile(values, 50) == 10
    assert eval_lib.percentile(values, 95) == 19
    assert eval_lib.percentile([7], 95) == 7


# --- Document setup -----------------------------------------------------------------------------


class FakeDocumentsApi(FakeApi):
    def __init__(self, timelines: dict[str, list[str]]) -> None:
        super().__init__([])
        self.timelines = timelines  # filename -> statuses returned on each poll

    def upload(self, token, path):
        return {"document_id": path.name, "status": "pending", "duplicate": False}

    def get_document(self, token, document_id):
        timeline = self.timelines[document_id]
        status = timeline.pop(0) if len(timeline) > 1 else timeline[0]
        return {"id": document_id, "status": status, "failure_code": None}


def test_upload_and_wait_until_ready_or_failed():
    api = FakeDocumentsApi({"a.pdf": ["pending", "processing", "ready"], "b.pdf": ["failed"]})
    sleeps: list[float] = []
    states = eval_lib.upload_and_wait(
        api, "t", [Path("a.pdf"), Path("b.pdf")], timeout_s=60, sleep=sleeps.append
    )
    assert {name: s["status"] for name, s in states.items()} == {
        "a.pdf": "ready",
        "b.pdf": "failed",
    }
    assert len(sleeps) == 2


def test_upload_and_wait_stops_at_the_timeout():
    api = FakeDocumentsApi({"a.pdf": ["processing"]})
    now = iter([0.0, 5.0, 11.0])
    states = eval_lib.upload_and_wait(
        api, "t", [Path("a.pdf")], timeout_s=10, sleep=lambda _: None, clock=lambda: next(now)
    )
    assert states["a.pdf"]["status"] == "processing"


# --- Loading the evaluation set ---------------------------------------------------------------


def test_the_sample_question_set_loads():
    cases = eval_lib.load_cases(REPO_ROOT / "sample_data" / "questions.yaml")
    assert len(cases) == 21
    by_id = {case.id: case for case in cases}
    injection = by_id["fuel-minimum-volume-injection"].expect
    assert injection.warnings_has == ("prompt_injection",)
    assert by_id["tire-delivery-term-contradiction"].expect.max_confidence == "medium"
    assert by_id["carrier-quick-pay-paraphrase"].soft


def test_select_by_ids_and_kinds():
    cases = eval_lib.load_cases(REPO_ROOT / "sample_data" / "questions.yaml")
    assert [c.id for c in eval_lib.select_cases(cases, only=["customs-brokerage-absent"])] == [
        "customs-brokerage-absent"
    ]
    assert {c.kind for c in eval_lib.select_cases(cases, kinds=["isolation"])} == {"isolation"}
    with pytest.raises(EvalSetError, match="unknown ids"):
        eval_lib.select_cases(cases, only=["nope"])


def test_new_kinds_are_accepted():
    case = eval_lib.parse_case(
        {
            "id": "x",
            "tenant": "tenant_eval",
            "question": "q",
            "kind": "near_miss",
            "expect": {"outcome": "insufficient_evidence"},
        }
    )
    assert case.kind == "near_miss" and not case.soft


@pytest.mark.parametrize(
    ("expect", "message"),
    [
        ({"outcome": "maybe"}, "expect.outcome"),
        ({"outcome": "answered", "answer_has": ["x"]}, "unknown expect keys: answer_has"),
        ({"outcome": "answered", "max_confidence": "very"}, "max_confidence"),
    ],
)
def test_schema_errors_are_explicit(expect, message):
    raw = {"id": "x", "tenant": "t", "question": "q", "kind": "lexical", "expect": expect}
    with pytest.raises(EvalSetError, match=message):
        eval_lib.parse_case(raw)

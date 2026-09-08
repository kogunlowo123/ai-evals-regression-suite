"""Run results: the sample policies, the aggregates, and the percentile."""

from __future__ import annotations

import pytest

from aievals.graders.base import Grade
from aievals.runner.result import CaseResult, RunResult, SampleResult

pytestmark = pytest.mark.unit


def sample(*, passed: bool = True, required: bool = True, latency_ms: float = 10.0, error=None):
    return SampleResult(
        response="x",
        grades=(Grade(grader="g", passed=passed, score=1.0 if passed else 0.0, required=required),),
        latency_ms=latency_ms,
        prompt_tokens=2,
        completion_tokens=3,
        error=error,
    )


class TestSampleResult:
    def test_a_sample_passes_when_every_required_grade_passes(self):
        assert sample(passed=True).passed

    def test_a_failing_required_grade_fails_the_sample(self):
        assert not sample(passed=False).passed

    def test_a_failing_optional_grade_does_not(self):
        # This is how a new expectation is introduced without breaking a gate.
        assert sample(passed=False, required=False).passed

    def test_an_error_fails_the_sample_whatever_the_grades(self):
        assert not sample(passed=True, error="boom").passed

    def test_it_serialises(self):
        payload = sample().as_dict()
        assert payload["passed"] is True
        assert payload["grades"][0]["grader"] == "g"


class TestSamplePolicies:
    def test_all_requires_every_sample_to_pass(self):
        mixed = CaseResult("c", (sample(passed=True), sample(passed=False)), policy="all")
        assert not mixed.passed

    def test_any_requires_only_one(self):
        mixed = CaseResult("c", (sample(passed=True), sample(passed=False)), policy="any")
        assert mixed.passed

    def test_majority_needs_more_than_half(self):
        two_of_three = CaseResult(
            "c",
            (sample(passed=True), sample(passed=True), sample(passed=False)),
            policy="majority",
        )
        assert two_of_three.passed
        one_of_two = CaseResult("c", (sample(passed=True), sample(passed=False)), policy="majority")
        assert not one_of_two.passed

    def test_a_case_with_no_samples_does_not_pass(self):
        assert not CaseResult("c", ()).passed

    def test_flakiness_is_reported_whatever_the_policy_decides(self):
        # A case that passes three times in five is a finding even in a run
        # whose policy calls it a pass.
        case = CaseResult("c", (sample(passed=True), sample(passed=False)), policy="any")
        assert case.passed
        assert case.flaky

    def test_a_unanimous_case_is_not_flaky(self):
        assert not CaseResult("c", (sample(), sample())).flaky


class TestCaseAggregates:
    def test_latency_is_the_mean_across_samples(self):
        case = CaseResult("c", (sample(latency_ms=10), sample(latency_ms=30)))
        assert case.latency_ms == 20

    def test_tokens_sum_across_samples(self):
        case = CaseResult("c", (sample(), sample()))
        assert case.total_tokens == 10

    def test_tokens_are_unknown_when_any_sample_did_not_report_them(self):
        unknown = SampleResult(response="", grades=(), latency_ms=1.0)
        assert CaseResult("c", (sample(), unknown)).total_tokens is None

    def test_failing_graders_are_deduplicated(self):
        case = CaseResult("c", (sample(passed=False), sample(passed=False)))
        assert case.failing_graders == ("g",)

    def test_the_first_error_is_surfaced(self):
        case = CaseResult("c", (sample(), sample(error="second")))
        assert case.error == "second"

    def test_a_case_with_no_error_reports_none(self):
        assert CaseResult("c", (sample(),)).error is None


class TestRunAggregates:
    def build(self, outcomes: list[bool], latencies: list[float] | None = None) -> RunResult:
        latencies = latencies or [10.0] * len(outcomes)
        cases = tuple(
            CaseResult(f"c{i}", (sample(passed=passed, latency_ms=latency),))
            for i, (passed, latency) in enumerate(zip(outcomes, latencies, strict=True))
        )
        return RunResult(suite_name="s", suite_digest="d", provider="p", model="m", cases=cases)

    def test_counts_and_rate(self):
        run = self.build([True, True, False, True])
        assert (run.total, run.passed, run.failed) == (4, 3, 1)
        assert run.pass_rate == pytest.approx(0.75)

    def test_an_empty_run_has_a_zero_rate_rather_than_dividing_by_zero(self):
        run = RunResult(suite_name="s", suite_digest="d", provider="p", model="m", cases=())
        assert run.pass_rate == 0.0
        assert run.p95_latency_ms() == 0.0

    def test_the_percentile_is_a_value_a_case_actually_took(self):
        # Nearest-rank, not interpolated: a budget should be breached by
        # something a reader can point at.
        run = self.build([True] * 20, [float(i) for i in range(1, 21)])
        assert run.p95_latency_ms() == 19.0

    def test_the_percentile_of_a_single_case_is_that_case(self):
        assert self.build([True], [42.0]).p95_latency_ms() == 42.0

    def test_outcomes_are_keyed_by_case_id(self):
        assert self.build([True, False]).outcomes() == {"c0": True, "c1": False}

    def test_a_case_can_be_looked_up_and_missed(self):
        run = self.build([True])
        assert run.case("c0") is not None
        assert run.case("absent") is None

    def test_total_tokens_are_unknown_when_a_case_did_not_report_them(self):
        run = RunResult(
            suite_name="s",
            suite_digest="d",
            provider="p",
            model="m",
            cases=(CaseResult("c", (SampleResult(response="", grades=(), latency_ms=1.0),)),),
        )
        assert run.total_tokens is None

    def test_it_serialises_with_the_headline_numbers(self):
        payload = self.build([True, False]).as_dict()
        assert payload["total"] == 2
        assert payload["pass_rate"] == 0.5
        assert len(payload["cases"]) == 2

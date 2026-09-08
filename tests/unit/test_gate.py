"""The gate rules.

The tests that matter most here are the ones asserting a build goes **red**. A
gate that only ever passes is indistinguishable from no gate, and this file is
the evidence that this one is not that.

The mode split is asserted directly: under replay any flip fails, under sampling
only a significant one does. Both directions are checked, because a gate that
fails on everything is as useless as one that fails on nothing.
"""

from __future__ import annotations

import pytest

from aievals.baseline.compare import Comparison
from aievals.errors import EXIT_GATE_FAILED, EXIT_OK
from aievals.gate import evaluate
from aievals.graders.base import Grade
from aievals.runner.result import CaseResult, RunResult, SampleResult
from aievals.stats.mcnemar import mcnemar
from aievals.stats.wilson import wilson_interval
from aievals.suite.models import Case, Suite, Thresholds

pytestmark = pytest.mark.unit

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def sample(*, passed: bool, latency_ms: float = 10.0, tokens: int | None = 5, error=None):
    grade = Grade(grader="contains_all", passed=passed, score=1.0 if passed else 0.0)
    return SampleResult(
        response="x",
        grades=(grade,),
        latency_ms=latency_ms,
        prompt_tokens=tokens,
        completion_tokens=tokens,
        error=error,
    )


def run_of(
    outcomes: dict[str, bool],
    *,
    sampled: bool = False,
    latency_ms: float = 10.0,
    tokens: int | None = 5,
    errors: set[str] | None = None,
) -> RunResult:
    errors = errors or set()
    cases = tuple(
        CaseResult(
            case_id=case_id,
            samples=(
                sample(
                    passed=passed,
                    latency_ms=latency_ms,
                    tokens=tokens,
                    error="boom" if case_id in errors else None,
                ),
            ),
        )
        for case_id, passed in outcomes.items()
    )
    return RunResult(
        suite_name="s",
        suite_digest=DIGEST,
        provider="replay",
        model="m",
        cases=cases,
        sampled=sampled,
    )


def suite_of(outcomes: dict[str, bool], **thresholds) -> Suite:
    return Suite(
        name="s",
        cases=tuple(Case(id=case_id, prompt="p") for case_id in outcomes),
        thresholds=Thresholds(**thresholds),
    )


def comparison_of(
    before: dict[str, bool],
    after: dict[str, bool],
    *,
    sampled: bool = False,
    stale: bool = False,
) -> Comparison:
    shared = set(before) & set(after)
    return Comparison(
        regressions=tuple(sorted(c for c in shared if before[c] and not after[c])),
        improvements=tuple(sorted(c for c in shared if not before[c] and after[c])),
        added=tuple(sorted(set(after) - set(before))),
        removed=tuple(sorted(set(before) - set(after))),
        stale=stale,
        baseline_digest=OTHER_DIGEST if stale else DIGEST,
        run_digest=DIGEST,
        baseline_pass_rate=sum(before.values()) / len(before),
        run_pass_rate=sum(after.values()) / len(after),
        interval=wilson_interval(sum(after.values()), len(after)),
        test=mcnemar(before, after) if sampled else None,
    )


def codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


class TestDeterministicMode:
    def test_a_single_flip_fails_the_build(self):
        before = {"a": True, "b": True}
        after = {"a": True, "b": False}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert not report.passed
        assert "REGRESSION" in codes(report)
        assert report.exit_code == EXIT_GATE_FAILED

    def test_an_unchanged_run_passes(self):
        outcomes = {"a": True, "b": True}
        report = evaluate(
            run_of(outcomes), suite_of(outcomes), comparison=comparison_of(outcomes, outcomes)
        )
        assert report.passed
        assert report.exit_code == EXIT_OK

    def test_an_improvement_alone_does_not_fail(self):
        before = {"a": False}
        after = {"a": True}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert report.passed

    def test_no_statistical_finding_is_produced_without_sampling(self):
        # A p-value over a deterministic comparison is decoration.
        before = {f"c{i}": True for i in range(20)}
        after = {f"c{i}": i > 0 for i in range(20)}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert "SIGNIFICANT_REGRESSION" not in codes(report)
        assert "REGRESSION" in codes(report)

    def test_the_finding_names_the_cases(self):
        before = {"alpha": True}
        after = {"alpha": False}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert "alpha" in report.failures[0].detail

    def test_many_regressions_are_summarised(self):
        before = {f"c{i}": True for i in range(20)}
        after = {f"c{i}": False for i in range(20)}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert "more" in report.failures[0].detail


class TestSampledMode:
    def test_one_flip_in_twenty_is_a_warning_not_a_failure(self):
        before = {f"c{i}": True for i in range(20)}
        after = {f"c{i}": i > 0 for i in range(20)}
        report = evaluate(
            run_of(after, sampled=True),
            suite_of(after),
            comparison=comparison_of(before, after, sampled=True),
        )
        assert report.passed
        assert "REGRESSION_NOT_SIGNIFICANT" in codes(report)

    def test_a_large_drop_fails_the_build(self):
        before = {f"c{i}": True for i in range(20)}
        after = {f"c{i}": i >= 8 for i in range(20)}
        report = evaluate(
            run_of(after, sampled=True),
            suite_of(after),
            comparison=comparison_of(before, after, sampled=True),
        )
        assert not report.passed
        assert "SIGNIFICANT_REGRESSION" in codes(report)

    def test_a_large_improvement_does_not_fail_the_build(self):
        before = {f"c{i}": False for i in range(20)}
        after = {f"c{i}": i < 8 for i in range(20)}
        report = evaluate(
            run_of(after, sampled=True),
            suite_of(after),
            comparison=comparison_of(before, after, sampled=True),
        )
        assert report.passed

    def test_no_flips_produces_no_regression_finding(self):
        outcomes = {"a": True}
        report = evaluate(
            run_of(outcomes, sampled=True),
            suite_of(outcomes),
            comparison=comparison_of(outcomes, outcomes, sampled=True),
        )
        assert "REGRESSION_NOT_SIGNIFICANT" not in codes(report)


class TestStaleBaseline:
    def test_a_digest_mismatch_fails_the_build(self):
        outcomes = {"a": True}
        report = evaluate(
            run_of(outcomes),
            suite_of(outcomes),
            comparison=comparison_of(outcomes, outcomes, stale=True),
        )
        assert not report.passed
        assert "STALE_BASELINE" in codes(report)

    def test_nothing_downstream_of_a_stale_baseline_is_reported(self):
        # A regression count derived from the wrong suite looks like a result.
        before = {"a": True}
        after = {"a": False}
        report = evaluate(
            run_of(after), suite_of(after), comparison=comparison_of(before, after, stale=True)
        )
        assert "REGRESSION" not in codes(report)

    def test_it_can_be_downgraded_deliberately(self):
        outcomes = {"a": True}
        report = evaluate(
            run_of(outcomes),
            suite_of(outcomes),
            comparison=comparison_of(outcomes, outcomes, stale=True),
            allow_stale_baseline=True,
        )
        assert report.passed
        assert report.warnings[0].code == "STALE_BASELINE"

    def test_downgrading_still_reports_the_regression(self):
        before = {"a": True}
        after = {"a": False}
        report = evaluate(
            run_of(after),
            suite_of(after),
            comparison=comparison_of(before, after, stale=True),
            allow_stale_baseline=True,
        )
        assert "REGRESSION" in codes(report)


class TestThresholds:
    def test_a_pass_rate_below_the_minimum_fails(self):
        outcomes = {"a": True, "b": False}
        report = evaluate(run_of(outcomes), suite_of(outcomes, min_pass_rate=0.9))
        assert not report.passed
        assert "PASS_RATE_BELOW_MINIMUM" in codes(report)

    def test_a_pass_rate_at_the_minimum_passes(self):
        outcomes = {"a": True, "b": False}
        report = evaluate(run_of(outcomes), suite_of(outcomes, min_pass_rate=0.5))
        assert report.passed

    def test_an_absent_minimum_is_not_checked(self):
        outcomes = {"a": False}
        assert evaluate(run_of(outcomes), suite_of(outcomes)).passed

    def test_the_command_line_can_override_the_suite(self):
        outcomes = {"a": True, "b": False}
        report = evaluate(
            run_of(outcomes), suite_of(outcomes, min_pass_rate=0.1), min_pass_rate=1.0
        )
        assert not report.passed

    def test_the_finding_names_the_failing_cases(self):
        outcomes = {"good": True, "bad": False}
        report = evaluate(run_of(outcomes), suite_of(outcomes, min_pass_rate=1.0))
        assert "bad" in report.failures[0].detail


class TestBudgets:
    def test_a_latency_budget_breach_fails(self):
        outcomes = {"a": True}
        report = evaluate(
            run_of(outcomes, latency_ms=5000), suite_of(outcomes, max_p95_latency_ms=1000)
        )
        assert "LATENCY_BUDGET_EXCEEDED" in codes(report)

    def test_a_latency_budget_within_bounds_passes(self):
        outcomes = {"a": True}
        report = evaluate(
            run_of(outcomes, latency_ms=100), suite_of(outcomes, max_p95_latency_ms=1000)
        )
        assert report.passed

    def test_a_token_budget_breach_fails(self):
        outcomes = {"a": True}
        report = evaluate(run_of(outcomes, tokens=1000), suite_of(outcomes, max_total_tokens=100))
        assert "TOKEN_BUDGET_EXCEEDED" in codes(report)

    def test_an_unmeasurable_token_budget_fails_rather_than_passing_silently(self):
        # A budget satisfied because nothing was counted is not a budget.
        outcomes = {"a": True}
        report = evaluate(run_of(outcomes, tokens=None), suite_of(outcomes, max_total_tokens=100))
        assert "TOKEN_BUDGET_UNMEASURABLE" in codes(report)
        assert not report.passed


class TestHealth:
    def test_a_case_that_errored_fails_the_build(self):
        # Nothing was measured, so a green build would be a lie.
        outcomes = {"a": False}
        report = evaluate(run_of(outcomes, errors={"a"}), suite_of(outcomes))
        assert "CASE_ERRORS" in codes(report)
        assert not report.passed

    def test_a_flaky_case_is_a_warning(self):
        case = CaseResult(
            case_id="a", samples=(sample(passed=True), sample(passed=False)), policy="any"
        )
        run = RunResult(
            suite_name="s",
            suite_digest=DIGEST,
            provider="p",
            model="m",
            cases=(case,),
            policy="any",
            sampled=True,
        )
        report = evaluate(run, suite_of({"a": True}))
        assert "FLAKY_CASES" in codes(report)
        assert report.passed

    def test_removed_cases_are_a_warning(self):
        before = {"a": True, "gone": True}
        after = {"a": True}
        report = evaluate(run_of(after), suite_of(after), comparison=comparison_of(before, after))
        assert "CASES_REMOVED" in codes(report)


class TestRendering:
    def test_a_finding_renders_with_its_detail(self):
        outcomes = {"a": True, "b": False}
        report = evaluate(run_of(outcomes), suite_of(outcomes, min_pass_rate=1.0))
        rendered = report.failures[0].render()
        assert "FAIL" in rendered
        assert "PASS_RATE_BELOW_MINIMUM" in rendered

    def test_the_report_serialises(self):
        outcomes = {"a": True}
        payload = evaluate(run_of(outcomes), suite_of(outcomes)).as_dict()
        assert payload["passed"] is True
        assert payload["run"]["total"] == 1

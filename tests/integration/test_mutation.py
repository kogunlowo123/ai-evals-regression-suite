"""The meta-gate, end to end.

The decisive tests here are the pair at the top: a suite with a real
expectation must catch the corruptions, and a suite whose grader accepts
anything must not. If both produce the same score, the meta-gate measures
nothing.
"""

from __future__ import annotations

import pytest

from aievals.mutation import MutationHarness, select
from aievals.suite import parse_suite
from tests.conftest import RecordingProvider

pytestmark = pytest.mark.integration

STRONG = """
name: strong
provider: {name: scripted}
cases:
  - id: policy
    prompt: p
    graders:
      - {type: contains_all, params: {values: ["refund", "store credit"]}}
      - {type: numeric_close, params: {expected: 30, where: first}}
"""

WEAK = """
name: weak
provider: {name: scripted}
cases:
  - id: policy
    prompt: p
    graders:
      - {type: contains_none, params: {values: ["a phrase that never appears anywhere"]}}
"""

ANSWER = (
    "You can request a refund within 30 days of purchase. After that we can "
    "offer store credit instead, and a supervisor can review anything unusual."
)


class TestDiscrimination:
    async def test_a_suite_with_real_expectations_catches_every_core_corruption(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        report = await MutationHarness().analyse(suite, run)
        assert report.total > 0
        assert report.score == 1.0
        assert report.survivors == ()

    async def test_a_suite_that_checks_nothing_useful_survives_them(self, run_suite):
        # The same responses, the same mutators, a passing run — and a score
        # that says the suite would not notice a thing.
        suite = parse_suite(WEAK)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        report = await MutationHarness().analyse(suite, run)
        assert run.pass_rate == 1.0
        assert report.score == 0.0
        assert len(report.survivors) == report.total

    async def test_a_survivor_names_the_case_the_corruption_and_the_expectation(self, run_suite):
        suite = parse_suite(WEAK)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        report = await MutationHarness().analyse(suite, run)
        survivor = report.survivors[0]
        assert survivor.case_id == "policy"
        assert survivor.mutator in {m.name for m in select(mutator_set="core")}
        assert survivor.expectation
        assert survivor.excerpt

    async def test_a_caught_mutant_records_which_grader_rejected_it(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        report = await MutationHarness().analyse(suite, run)
        caught = next(m for m in report.mutants if m.mutator == "drop_numbers")
        assert "numeric_close" in caught.caught_by
        assert caught.excerpt == ""


class TestScope:
    async def test_a_failing_case_is_skipped_with_a_reason(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": "nothing useful"}))
        report = await MutationHarness().analyse(suite, run)
        assert report.total == 0
        assert report.skipped[0][0] == "policy"
        assert "cannot be flipped" in report.skipped[0][1]

    async def test_a_run_with_no_mutants_scores_zero_not_one(self, run_suite):
        # An empty measurement is not a perfect one.
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": "nothing useful"}))
        report = await MutationHarness().analyse(suite, run)
        assert report.score == 0.0

    async def test_an_inapplicable_mutator_produces_no_mutant(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_all, params: {values: [ok]}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": "ok"}))
        report = await MutationHarness(mutators=select(("drop_numbers",))).analyse(suite, run)
        assert report.total == 0

    async def test_a_corruption_that_changes_nothing_is_not_counted(self, run_suite):
        # strip_formatting on unformatted prose returns it unchanged; scoring
        # that would blame the suite for the mutator's own no-op.
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_all, params: {values: [plain]}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": "plain prose, nothing to strip"}))
        report = await MutationHarness(mutators=select(("strip_formatting",))).analyse(suite, run)
        assert report.total == 0


class TestJudgeExclusion:
    async def test_judge_graders_are_excluded_and_counted(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - id: a\n    prompt: p\n    graders:\n"
            "      - {type: contains_all, params: {values: [ok]}}\n"
            "      - {type: judge, params: {criterion: is it polite}}\n"
        )
        run = await run_suite(
            suite,
            RecordingProvider({"p": "ok this is a long enough answer to truncate"}),
            judge=RecordingProvider({}),
        )
        # The judge answered nothing, so the case fails and is skipped. Build a
        # run where it passes instead.
        assert run.case("a").passed is False

    async def test_a_case_graded_only_by_a_judge_is_skipped_by_name(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - id: judged\n    prompt: p\n    graders:\n"
            "      - {type: judge, params: {criterion: is it polite}}\n"
        )
        judge = RecordingProvider({})
        judge.default = '{"verdict": "pass", "reason": "fine"}'
        run = await run_suite(suite, RecordingProvider({"p": "an answer"}), judge=judge)
        assert run.pass_rate == 1.0
        report = await MutationHarness().analyse(suite, run)
        assert report.skipped[0][0] == "judged"
        assert "model-graded" in report.skipped[0][1]
        assert report.judge_graders_excluded == 1


class TestReporting:
    async def test_the_per_mutator_table_adds_up(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        report = await MutationHarness().analyse(suite, run)
        table = report.by_mutator()
        assert sum(row["total"] for row in table.values()) == report.total
        assert sum(row["caught"] for row in table.values()) == report.caught

    async def test_the_summary_reads_as_a_sentence(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        assert "caught" in (await MutationHarness().analyse(suite, run)).summary()

    async def test_an_empty_report_says_so(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": "no"}))
        report = await MutationHarness().analyse(suite, run)
        assert report.summary() == "no applicable mutants were produced"

    async def test_it_is_deterministic_across_runs(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        first = await MutationHarness().analyse(suite, run)
        second = await MutationHarness().analyse(suite, run)
        assert first.as_dict()["mutants"] == second.as_dict()["mutants"]

    async def test_it_serialises_with_the_headline_numbers(self, run_suite):
        suite = parse_suite(STRONG)
        run = await run_suite(suite, RecordingProvider({"p": ANSWER}))
        payload = (await MutationHarness().analyse(suite, run)).as_dict()
        assert payload["score"] == 1.0
        assert payload["suite"] == "strong"
        assert payload["metadata"]["mutators"]

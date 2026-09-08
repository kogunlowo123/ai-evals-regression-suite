"""Baselines and the comparison built from them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aievals.baseline import Baseline, compare
from aievals.errors import BaselineError
from aievals.runner import RunOptions
from aievals.suite import parse_suite
from tests.conftest import RecordingProvider

pytestmark = pytest.mark.integration

SUITE = """
name: cmp
provider: {name: scripted}
cases:
  - {id: a, prompt: pa, graders: [{type: contains_all, params: {values: [ok]}}]}
  - {id: b, prompt: pb, graders: [{type: contains_all, params: {values: [ok]}}]}
  - {id: c, prompt: pc, graders: [{type: contains_all, params: {values: [ok]}}]}
"""

ALL_GOOD = {"pa": "ok", "pb": "ok", "pc": "ok"}


@pytest.fixture
def suite():
    return parse_suite(SUITE)


class TestSnapshot:
    async def test_a_baseline_round_trips(self, suite, run_suite, tmp_path: Path):
        run = await run_suite(suite, RecordingProvider(ALL_GOOD))
        path = Baseline.from_run(run, note="first").save(tmp_path / "b.json")
        loaded = Baseline.load(path)
        assert loaded.outcomes() == {"a": True, "b": True, "c": True}
        assert loaded.suite_digest == run.suite_digest
        assert loaded.pass_rate == 1.0

    async def test_it_records_the_note_and_the_time(self, suite, run_suite, tmp_path: Path):
        run = await run_suite(suite, RecordingProvider(ALL_GOOD))
        path = Baseline.from_run(run, note="the reason").save(tmp_path / "b.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["note"] == "the reason"
        assert Baseline.load(path).recorded_at

    async def test_it_does_not_store_model_output(self, suite, run_suite, tmp_path: Path):
        # A baseline holds verdicts. Storing responses would put model output
        # into version control and make the file enormous.
        run = await run_suite(suite, RecordingProvider({"pa": "ok secret-ish text"}))
        path = Baseline.from_run(run).save(tmp_path / "b.json")
        assert "secret-ish" not in path.read_text(encoding="utf-8")

    async def test_case_order_is_normalised(self, suite, run_suite, tmp_path: Path):
        run = await run_suite(suite, RecordingProvider(ALL_GOOD))
        payload = json.loads(
            Baseline.from_run(run).save(tmp_path / "b.json").read_text(encoding="utf-8")
        )
        assert list(payload["cases"]) == sorted(payload["cases"])

    def test_a_missing_baseline_is_reported_with_a_remedy(self, tmp_path: Path):
        with pytest.raises(BaselineError) as caught:
            Baseline.load(tmp_path / "absent.json")
        assert "aievals baseline" in caught.value.remedy

    def test_a_wrong_version_is_refused(self, tmp_path: Path):
        path = tmp_path / "b.json"
        path.write_text(json.dumps({"baseline_version": 99}), encoding="utf-8")
        with pytest.raises(BaselineError, match="version"):
            Baseline.load(path)

    def test_a_baseline_without_a_digest_is_refused(self, tmp_path: Path):
        # Without one it cannot be checked against a suite, which is the whole
        # protection against comparing with the wrong thing.
        path = tmp_path / "b.json"
        path.write_text(json.dumps({"baseline_version": 1, "cases": {}}), encoding="utf-8")
        with pytest.raises(BaselineError, match="no suite digest"):
            Baseline.load(path)

    def test_malformed_json_is_refused(self, tmp_path: Path):
        path = tmp_path / "b.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(BaselineError, match="could not be read"):
            Baseline.load(path)


class TestComparison:
    async def compare_runs(
        self, suite, run_suite, before: dict[str, str], after: dict[str, str], **kwargs
    ):
        first = await run_suite(suite, RecordingProvider(before), **kwargs)
        second = await run_suite(suite, RecordingProvider(after), **kwargs)
        return compare(Baseline.from_run(first), second)

    async def test_an_unchanged_run_reports_no_change(self, suite, run_suite):
        result = await self.compare_runs(suite, run_suite, ALL_GOOD, ALL_GOOD)
        assert result.unchanged
        assert "no change" in result.summary()

    async def test_a_flip_is_reported_as_a_regression(self, suite, run_suite):
        result = await self.compare_runs(suite, run_suite, ALL_GOOD, {**ALL_GOOD, "pb": "no"})
        assert result.regressions == ("b",)
        assert result.pass_rate_delta < 0

    async def test_a_fix_is_reported_as_an_improvement(self, suite, run_suite):
        result = await self.compare_runs(suite, run_suite, {**ALL_GOOD, "pb": "no"}, ALL_GOOD)
        assert result.improvements == ("b",)
        assert result.pass_rate_delta > 0

    async def test_a_digest_mismatch_is_reported_as_stale(self, suite, run_suite):
        first = await run_suite(suite, RecordingProvider(ALL_GOOD))
        edited = parse_suite(SUITE.replace("prompt: pa", "prompt: changed"))
        second = await run_suite(edited, RecordingProvider({**ALL_GOOD, "changed": "ok"}))
        assert compare(Baseline.from_run(first), second).stale

    async def test_added_and_removed_cases_are_separate_from_regressions(self, suite, run_suite):
        # Folding them into the regression count would put suite edits into the
        # signal for a code change.
        first = await run_suite(suite, RecordingProvider(ALL_GOOD))
        smaller = parse_suite(
            "name: cmp\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: pa, graders: "
            "[{type: contains_all, params: {values: [ok]}}]}\n"
            "  - {id: d, prompt: pd, graders: "
            "[{type: contains_all, params: {values: [ok]}}]}\n"
        )
        second = await run_suite(smaller, RecordingProvider({"pa": "ok", "pd": "ok"}))
        result = compare(Baseline.from_run(first), second)
        assert result.added == ("d",)
        assert result.removed == ("b", "c")
        assert result.regressions == ()

    async def test_no_statistical_test_is_produced_for_a_single_sample_run(self, suite, run_suite):
        result = await self.compare_runs(suite, run_suite, ALL_GOOD, ALL_GOOD)
        assert result.test is None

    async def test_a_sampled_run_produces_the_paired_test(self, suite, run_suite):
        options = RunOptions(samples=2)
        result = await self.compare_runs(suite, run_suite, ALL_GOOD, ALL_GOOD, options=options)
        assert result.test is not None
        assert result.test.discordant == 0

    async def test_the_interval_is_reported_alongside_the_rate(self, suite, run_suite):
        result = await self.compare_runs(suite, run_suite, ALL_GOOD, ALL_GOOD)
        assert result.interval.trials == 3
        assert result.interval.low < 1.0

    async def test_it_serialises(self, suite, run_suite):
        payload = (await self.compare_runs(suite, run_suite, ALL_GOOD, ALL_GOOD)).as_dict()
        assert payload["regressions"] == []
        assert payload["test"] is None

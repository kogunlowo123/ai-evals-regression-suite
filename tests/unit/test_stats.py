"""The paired test and the interval.

Where a closed form is known, the expected value is written out rather than
computed by the code under test — a test that reimplements the implementation
passes whatever the implementation does.
"""

from __future__ import annotations

import math

import pytest

from aievals.stats import binomial_two_sided_p, mcnemar, wilson_interval

pytestmark = pytest.mark.unit


class TestExactBinomial:
    def test_no_discordant_pairs_gives_no_evidence_of_change(self):
        assert binomial_two_sided_p(0, 0) == 1.0

    def test_a_perfectly_even_split_is_maximally_unsurprising(self):
        assert binomial_two_sided_p(5, 10) == 1.0

    def test_one_of_one_is_not_significant(self):
        # A single flip is a coin landing once. p = 2 * P(X <= 0) = 2 * 0.5 = 1.
        assert binomial_two_sided_p(1, 1) == 1.0

    def test_five_of_five_is_significant_at_the_usual_level(self):
        # p = 2 * (1/32) = 0.0625 — notably *not* below 0.05, which is exactly
        # the sort of thing an approximation would get wrong.
        assert binomial_two_sided_p(5, 5) == pytest.approx(0.0625)

    def test_six_of_six_crosses_the_usual_level(self):
        assert binomial_two_sided_p(6, 6) == pytest.approx(2 / 64)

    def test_the_value_matches_the_closed_form(self):
        # 2 * sum over k <= 2 of C(10, k) / 2^10
        expected = 2 * (math.comb(10, 0) + math.comb(10, 1) + math.comb(10, 2)) / 1024
        assert binomial_two_sided_p(2, 10) == pytest.approx(expected)

    def test_it_is_symmetric_in_the_two_directions(self):
        assert binomial_two_sided_p(3, 10) == binomial_two_sided_p(7, 10)

    def test_it_never_exceeds_one(self):
        for trials in range(0, 30):
            for successes in range(trials + 1):
                assert 0.0 <= binomial_two_sided_p(successes, trials) <= 1.0

    @pytest.mark.parametrize(("successes", "trials"), [(-1, 5), (6, 5), (0, -1)])
    def test_impossible_counts_are_refused(self, successes: int, trials: int):
        with pytest.raises(ValueError, match="must"):
            binomial_two_sided_p(successes, trials)


class TestMcNemar:
    def test_identical_runs_produce_no_discordant_pairs(self):
        outcomes = {"a": True, "b": False}
        result = mcnemar(outcomes, outcomes)
        assert result.discordant == 0
        assert result.direction == "unchanged"
        assert not result.significant

    def test_it_counts_only_the_discordant_pairs(self):
        before = {"a": True, "b": True, "c": False, "d": False}
        after = {"a": True, "b": False, "c": True, "d": False}
        result = mcnemar(before, after)
        assert result.regressions == 1
        assert result.improvements == 1
        assert result.discordant == 2

    def test_cases_present_on_only_one_side_are_ignored(self):
        # Otherwise adding a case to the suite would show up in the
        # significance of a code change.
        result = mcnemar({"a": True, "gone": True}, {"a": True, "new": False})
        assert result.discordant == 0

    def test_a_large_one_sided_change_is_significant(self):
        before = {f"c{i}": True for i in range(20)}
        after = {f"c{i}": i >= 8 for i in range(20)}
        result = mcnemar(before, after)
        assert result.regressions == 8
        assert result.improvements == 0
        assert result.significant
        assert result.direction == "worse"
        assert result.significant_regression

    def test_a_large_improvement_is_significant_but_not_a_regression(self):
        # A build that fails on good news gets its gate deleted.
        before = {f"c{i}": False for i in range(20)}
        after = {f"c{i}": i < 8 for i in range(20)}
        result = mcnemar(before, after)
        assert result.significant
        assert result.direction == "better"
        assert not result.significant_regression

    def test_one_flip_in_twenty_is_not_significant(self):
        before = {f"c{i}": True for i in range(20)}
        after = {**before, "c0": False}
        result = mcnemar(before, after)
        assert not result.significant
        assert not result.significant_regression

    def test_alpha_is_honoured(self):
        before = {f"c{i}": True for i in range(5)}
        after = {f"c{i}": False for i in range(5)}
        assert not mcnemar(before, after, alpha=0.05).significant
        assert mcnemar(before, after, alpha=0.10).significant

    def test_an_impossible_alpha_is_refused(self):
        with pytest.raises(ValueError, match="alpha"):
            mcnemar({}, {}, alpha=1.0)

    def test_the_summary_reads_as_a_sentence(self):
        before = {"a": True}
        after = {"a": False}
        assert "regressed" in mcnemar(before, after).summary()
        assert mcnemar(before, before).summary() == "no case changed verdict"

    def test_it_serialises(self):
        payload = mcnemar({"a": True}, {"a": False}).as_dict()
        assert payload["test"] == "mcnemar_exact"
        assert payload["regressions"] == 1


class TestWilsonInterval:
    def test_a_perfect_score_does_not_produce_a_zero_width_interval(self):
        # The textbook normal approximation gives [1, 1] here, which is the
        # claim that 20 of 20 proves a 100% pass rate.
        interval = wilson_interval(20, 20)
        assert interval.point == 1.0
        assert interval.low < 1.0
        assert interval.high == 1.0

    def test_a_zero_score_does_not_go_below_zero(self):
        interval = wilson_interval(0, 20)
        assert interval.low == 0.0
        assert interval.high > 0.0

    def test_the_interval_brackets_the_point_estimate(self):
        for successes in range(0, 21):
            interval = wilson_interval(successes, 20)
            assert interval.low <= interval.point <= interval.high

    def test_more_trials_narrow_the_interval(self):
        assert wilson_interval(90, 100).width < wilson_interval(9, 10).width

    def test_a_higher_confidence_widens_the_interval(self):
        assert (
            wilson_interval(9, 10, confidence=0.99).width
            > wilson_interval(9, 10, confidence=0.90).width
        )

    def test_the_midpoint_case_matches_the_published_value(self):
        # 50/100 at 95%: the Wilson interval is approximately [0.4038, 0.5962].
        interval = wilson_interval(50, 100)
        assert interval.low == pytest.approx(0.4038, abs=5e-4)
        assert interval.high == pytest.approx(0.5962, abs=5e-4)

    def test_nothing_measured_gives_the_whole_unit_interval(self):
        interval = wilson_interval(0, 0)
        assert (interval.low, interval.high) == (0.0, 1.0)

    def test_the_summary_shows_the_counts_and_the_bounds(self):
        assert "18/20" in wilson_interval(18, 20).summary()

    @pytest.mark.parametrize(
        ("successes", "trials", "confidence"), [(-1, 5, 0.95), (6, 5, 0.95), (1, 5, 1.0)]
    )
    def test_impossible_arguments_are_refused(self, successes: int, trials: int, confidence: float):
        with pytest.raises(ValueError, match="must"):
            wilson_interval(successes, trials, confidence=confidence)

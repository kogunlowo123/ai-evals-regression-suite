"""Every text grader, with a negative control for each.

A grader test that only shows the grader passing proves nothing: a grader that
returns ``passed=True`` unconditionally satisfies it. Each class below has at
least one case the grader must **reject**, and that is the assertion that
matters.
"""

from __future__ import annotations

from typing import Any

import pytest

from aievals.errors import GraderError
from aievals.graders.base import GradeContext
from aievals.graders.registry import build_grader
from aievals.suite.models import Case, GraderSpec

pytestmark = pytest.mark.unit

CASE = Case(id="c", prompt="p")
CONTEXT = GradeContext()


async def grade(kind: str, params: dict[str, Any], response: str):
    return await build_grader(GraderSpec(type=kind, params=params)).grade(response, CASE, CONTEXT)


class TestContainsAll:
    async def test_passes_when_every_value_is_present(self):
        result = await grade("contains_all", {"values": ["cat", "dog"]}, "a cat and a dog")
        assert result.passed
        assert result.score == 1.0

    async def test_rejects_a_response_missing_one_value(self):
        result = await grade("contains_all", {"values": ["cat", "dog"]}, "only a cat")
        assert not result.passed
        assert "'dog'" in result.detail

    async def test_the_score_is_the_fraction_found(self):
        result = await grade(
            "contains_all", {"values": ["one", "two", "three", "four"]}, "one and two"
        )
        assert result.score == pytest.approx(0.5)

    async def test_case_sensitivity_is_on_by_default(self):
        result = await grade("contains_all", {"values": ["Cat"]}, "a cat")
        assert not result.passed

    async def test_case_sensitivity_can_be_turned_off(self):
        result = await grade("contains_all", {"values": ["Cat"], "case_sensitive": False}, "a cat")
        assert result.passed

    async def test_whitespace_normalisation_is_opt_in(self):
        strict = await grade("contains_all", {"values": ["a b"]}, "a\n b")
        assert not strict.passed
        relaxed = await grade(
            "contains_all", {"values": ["a b"], "normalize_whitespace": True}, "a\n b"
        )
        assert relaxed.passed

    def test_a_bare_string_is_refused_because_it_iterates_as_characters(self):
        # `values: "hello"` would check for h, e, l, l, o and pass on almost
        # anything. Silently accepting it is a suite that stopped checking.
        with pytest.raises(GraderError, match="list of strings"):
            build_grader(GraderSpec(type="contains_all", params={"values": "hello"}))

    def test_an_empty_value_list_is_refused(self):
        with pytest.raises(GraderError, match="pass everything"):
            build_grader(GraderSpec(type="contains_all", params={"values": []}))

    def test_a_missing_values_parameter_is_refused(self):
        with pytest.raises(GraderError, match="values"):
            build_grader(GraderSpec(type="contains_all", params={}))

    async def test_many_missing_values_are_summarised(self):
        result = await grade("contains_all", {"values": list("abcdefgh")}, "")
        assert "more" in result.detail


class TestContainsNone:
    async def test_passes_when_nothing_forbidden_appears(self):
        result = await grade("contains_none", {"values": ["sorry"]}, "here is the answer")
        assert result.passed

    async def test_rejects_a_response_containing_a_forbidden_value(self):
        result = await grade("contains_none", {"values": ["sorry"]}, "sorry, I cannot")
        assert not result.passed
        assert "'sorry'" in result.detail

    async def test_case_insensitive_matching_catches_a_capitalised_phrase(self):
        result = await grade(
            "contains_none",
            {"values": ["as an ai"], "case_sensitive": False},
            "As an AI language model, I must say",
        )
        assert not result.passed


class TestExact:
    async def test_passes_on_an_identical_response(self):
        result = await grade("exact", {"value": "42"}, "42")
        assert result.passed

    async def test_rejects_a_different_response(self):
        result = await grade("exact", {"value": "42"}, "43")
        assert not result.passed
        assert "'43'" in result.detail

    async def test_surrounding_whitespace_is_stripped_by_default(self):
        result = await grade("exact", {"value": "42"}, "  42\n")
        assert result.passed

    async def test_stripping_can_be_turned_off(self):
        result = await grade("exact", {"value": "42", "strip": False}, "42\n")
        assert not result.passed

    async def test_a_long_response_is_clipped_in_the_message(self):
        result = await grade("exact", {"value": "x"}, "y" * 500)
        assert len(result.detail) < 300

    def test_a_missing_value_is_refused(self):
        with pytest.raises(GraderError, match="value"):
            build_grader(GraderSpec(type="exact", params={}))


class TestRegex:
    async def test_passes_when_the_pattern_matches(self):
        result = await grade("regex", {"pattern": r"\bAC-\d{5}\b"}, "order AC-48213 found")
        assert result.passed
        assert "offset" in result.detail

    async def test_rejects_a_response_the_pattern_does_not_match(self):
        result = await grade("regex", {"pattern": r"\bAC-\d{5}\b"}, "order AC-482 found")
        assert not result.passed

    async def test_must_match_false_inverts_the_verdict(self):
        rejected = await grade("regex", {"pattern": "secret", "must_match": False}, "a secret")
        assert not rejected.passed
        accepted = await grade("regex", {"pattern": "secret", "must_match": False}, "nothing")
        assert accepted.passed

    async def test_flags_are_applied(self):
        result = await grade(
            "regex", {"pattern": "^abc$", "flags": ["IGNORECASE", "MULTILINE"]}, "x\nABC\ny"
        )
        assert result.passed

    def test_an_uncompilable_pattern_is_refused_at_build_time(self):
        with pytest.raises(GraderError, match="does not compile"):
            build_grader(GraderSpec(type="regex", params={"pattern": "("}))

    def test_an_unknown_flag_is_refused(self):
        with pytest.raises(GraderError, match="unknown regex flag"):
            build_grader(GraderSpec(type="regex", params={"pattern": "a", "flags": ["LOCALE"]}))

    def test_a_very_long_pattern_is_refused(self):
        with pytest.raises(GraderError, match="over the"):
            build_grader(GraderSpec(type="regex", params={"pattern": "a" * 1001}))

    def test_an_empty_pattern_is_refused(self):
        with pytest.raises(GraderError, match="non-empty"):
            build_grader(GraderSpec(type="regex", params={"pattern": ""}))


class TestOrdering:
    async def test_passes_when_values_appear_in_order(self):
        result = await grade(
            "ordering", {"values": ["first", "second", "third"]}, "first, then second, then third"
        )
        assert result.passed

    async def test_rejects_values_that_appear_out_of_order(self):
        result = await grade(
            "ordering", {"values": ["first", "second"]}, "second comes before first here"
        )
        assert not result.passed
        assert "before it should" in result.detail

    async def test_distinguishes_missing_from_out_of_order(self):
        result = await grade("ordering", {"values": ["first", "second"]}, "only first")
        assert "is missing" in result.detail

    async def test_the_score_reflects_how_far_it_got(self):
        result = await grade(
            "ordering", {"values": ["one", "two", "three", "four"]}, "one then two"
        )
        assert 0 < result.score < 1

    def test_a_single_value_is_refused_because_one_thing_has_no_order(self):
        with pytest.raises(GraderError, match="at least two"):
            build_grader(GraderSpec(type="ordering", params={"values": ["only"]}))


class TestGradeMetadata:
    async def test_the_grade_carries_the_registered_name(self):
        result = await grade("contains_all", {"values": ["a"]}, "a")
        assert result.grader == "contains_all"

    async def test_required_is_carried_through_from_the_specification(self):
        spec = GraderSpec(type="contains_all", params={"values": ["a"]}, required=False)
        result = await build_grader(spec).grade("a", CASE, CONTEXT)
        assert result.required is False

    def test_a_score_outside_the_unit_interval_is_refused(self):
        from aievals.graders.base import Grade

        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            Grade(grader="x", passed=True, score=1.5)

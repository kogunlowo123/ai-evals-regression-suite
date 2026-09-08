"""The structured graders, each with the cases it must reject."""

from __future__ import annotations

from typing import Any

import pytest

from aievals.errors import GraderError
from aievals.graders.base import GradeContext
from aievals.graders.registry import build_grader
from aievals.graders.structured import extract_json_text
from aievals.suite.models import Case, GraderSpec

pytestmark = pytest.mark.unit

CASE = Case(id="c", prompt="p")
CONTEXT = GradeContext()

TRIAGE_SCHEMA = {
    "type": "object",
    "required": ["category", "severity"],
    "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": ["billing", "shipping"]},
        "severity": {"type": "integer", "minimum": 1, "maximum": 5},
    },
}


async def grade(kind: str, params: dict[str, Any], response: str):
    return await build_grader(GraderSpec(type=kind, params=params)).grade(response, CASE, CONTEXT)


class TestExtraction:
    def test_a_bare_object_is_returned_unchanged(self):
        assert extract_json_text('{"a": 1}') == '{"a": 1}'

    def test_a_fenced_block_is_unwrapped(self):
        assert extract_json_text('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_an_untagged_fence_is_unwrapped(self):
        assert extract_json_text('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_an_object_embedded_in_prose_is_found(self):
        assert extract_json_text('Here you go: {"a": 1} — hope that helps') == '{"a": 1}'

    def test_an_array_is_found(self):
        assert extract_json_text("result: [1, 2, 3]") == "[1, 2, 3]"

    def test_prose_with_no_json_comes_back_stripped(self):
        assert extract_json_text("  no json here  ") == "no json here"


class TestJsonSchema:
    async def test_passes_a_valid_document(self):
        result = await grade(
            "json_schema", {"schema": TRIAGE_SCHEMA}, '{"category": "billing", "severity": 3}'
        )
        assert result.passed

    async def test_rejects_a_response_that_is_not_json(self):
        result = await grade("json_schema", {"schema": TRIAGE_SCHEMA}, "the category is billing")
        assert not result.passed
        assert "not JSON" in result.detail

    async def test_rejects_a_missing_required_property(self):
        result = await grade("json_schema", {"schema": TRIAGE_SCHEMA}, '{"category": "billing"}')
        assert not result.passed
        assert "severity" in result.detail

    async def test_rejects_a_value_outside_the_enum(self):
        result = await grade(
            "json_schema", {"schema": TRIAGE_SCHEMA}, '{"category": "weather", "severity": 1}'
        )
        assert not result.passed

    async def test_rejects_an_unexpected_property(self):
        result = await grade(
            "json_schema",
            {"schema": TRIAGE_SCHEMA},
            '{"category": "billing", "severity": 1, "extra": true}',
        )
        assert not result.passed
        assert "unexpected property" in result.detail

    async def test_rejects_truncated_json(self):
        result = await grade(
            "json_schema", {"schema": TRIAGE_SCHEMA}, '{"category": "billing", "severity": 3'
        )
        assert not result.passed

    async def test_reports_several_problems_at_once(self):
        result = await grade("json_schema", {"schema": TRIAGE_SCHEMA}, "{}")
        assert result.detail.count(";") >= 1

    def test_a_schema_using_an_unimplemented_keyword_is_refused(self):
        # Silently ignoring oneOf would make the grader accept every document
        # against a schema whose entire meaning is that keyword.
        with pytest.raises(GraderError, match="oneOf"):
            build_grader(
                GraderSpec(type="json_schema", params={"schema": {"oneOf": [{"type": "string"}]}})
            )

    def test_a_missing_schema_is_refused(self):
        with pytest.raises(GraderError, match="schema"):
            build_grader(GraderSpec(type="json_schema", params={}))


class TestNumericClose:
    async def test_passes_on_an_exact_figure(self):
        result = await grade("numeric_close", {"expected": 30}, "within 30 days")
        assert result.passed

    async def test_rejects_a_wrong_figure(self):
        result = await grade("numeric_close", {"expected": 30}, "within 60 days")
        assert not result.passed
        assert "off by 30" in result.detail

    async def test_rejects_a_response_with_no_number(self):
        result = await grade("numeric_close", {"expected": 30}, "within a month")
        assert not result.passed
        assert "no number" in result.detail

    async def test_an_absolute_tolerance_is_honoured(self):
        result = await grade("numeric_close", {"expected": 30, "tolerance": 2}, "about 31 days")
        assert result.passed

    async def test_a_relative_tolerance_is_honoured(self):
        result = await grade(
            "numeric_close", {"expected": 100, "tolerance": 0.1, "relative": True}, "about 108"
        )
        assert result.passed

    async def test_where_last_reads_the_final_number(self):
        result = await grade(
            "numeric_close", {"expected": 7, "where": "last"}, "first 3, then 5, finally 7"
        )
        assert result.passed

    async def test_where_only_rejects_an_ambiguous_answer(self):
        result = await grade("numeric_close", {"expected": 7, "where": "only"}, "3 or maybe 7")
        assert not result.passed
        assert "'only' was required" in result.detail

    async def test_thousands_separators_are_read(self):
        result = await grade("numeric_close", {"expected": 1234567}, "that is 1,234,567 exactly")
        assert result.passed

    async def test_a_version_suffix_is_not_read_as_a_number(self):
        # "v2" must not satisfy a case about a quantity.
        result = await grade("numeric_close", {"expected": 30}, "see v2 of the policy")
        assert not result.passed

    def test_a_negative_tolerance_is_refused(self):
        with pytest.raises(GraderError, match="negative"):
            build_grader(GraderSpec(type="numeric_close", params={"expected": 1, "tolerance": -1}))

    def test_a_relative_tolerance_around_zero_is_refused(self):
        with pytest.raises(GraderError, match="zero"):
            build_grader(
                GraderSpec(
                    type="numeric_close",
                    params={"expected": 0, "tolerance": 0.1, "relative": True},
                )
            )

    def test_an_unknown_where_is_refused(self):
        with pytest.raises(GraderError, match="first, last, only"):
            build_grader(
                GraderSpec(type="numeric_close", params={"expected": 1, "where": "middle"})
            )

    def test_a_boolean_expected_is_refused(self):
        # True is an int in Python; accepting it would grade against 1.
        with pytest.raises(GraderError, match="number"):
            build_grader(GraderSpec(type="numeric_close", params={"expected": True}))


class TestSetOverlap:
    async def test_passes_at_full_recall(self):
        result = await grade(
            "set_overlap",
            {"expected": ["alpha", "beta"], "min_recall": 1.0},
            "both alpha and beta apply",
        )
        assert result.passed

    async def test_rejects_partial_recall_below_the_threshold(self):
        result = await grade(
            "set_overlap", {"expected": ["alpha", "beta"], "min_recall": 1.0}, "only alpha"
        )
        assert not result.passed
        assert "'beta'" in result.detail

    async def test_partial_recall_above_the_threshold_passes(self):
        result = await grade(
            "set_overlap",
            {"expected": ["alpha", "beta", "gamma", "delta"], "min_recall": 0.5},
            "alpha and beta",
        )
        assert result.passed
        assert result.score == pytest.approx(0.5)

    async def test_matching_is_case_insensitive_by_default(self):
        result = await grade("set_overlap", {"expected": ["Alpha"]}, "alpha")
        assert result.passed

    def test_a_bare_string_is_refused(self):
        with pytest.raises(GraderError, match="not a single string"):
            build_grader(GraderSpec(type="set_overlap", params={"expected": "alpha"}))

    def test_a_recall_of_zero_is_refused_because_it_passes_everything(self):
        with pytest.raises(GraderError, match=r"\(0, 1\]"):
            build_grader(
                GraderSpec(type="set_overlap", params={"expected": ["a"], "min_recall": 0.0})
            )

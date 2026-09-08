"""The static guard on regular expressions from suite files.

A suite file is untrusted input and may declare a ``regex`` grader. ``(a+)+b``
against forty ``a`` characters is one ``re.search`` that does not return, and no
timeout above it helps: ``asyncio.wait_for`` around ``asyncio.to_thread``
abandons the coroutine but cannot stop the thread, and Python offers no way to
interrupt a running match. So the check has to happen before compilation.

The two halves are equally important. Missing a catastrophic pattern hangs a CI
job until it is killed; refusing an ordinary one makes the grader unusable and
gets the guard removed.
"""

from __future__ import annotations

import time

import pytest

from aievals.errors import GraderError
from aievals.graders.redos import catastrophic_shape
from aievals.graders.registry import build_grader
from aievals.suite import parse_suite
from aievals.suite.models import GraderSpec

pytestmark = pytest.mark.security

CATASTROPHIC = [
    r"(a+)+b",
    r"(a*)*b",
    r"(\d+)+$",
    r"([a-z]*)*!",
    r"(a|a)+b",
    r"(a|ab)*c",
    r"^(\w+\s?)*$",
    r"(x+x+)+y",
    r"(.*)*z",
    r"((a)*)*b",
]

SAFE = [
    r"\bAC-\d{5}\b",
    r"^\d{4}-\d{2}-\d{2}$",
    r"(foo|bar|baz)",
    r"[A-Za-z0-9_]+@[A-Za-z0-9_.]+",
    r"\s*\d+\s*",
    r"(?:https?://)\S+",
    r"a{3}",
    r"(cat|dog)s?",
    r"^\s*#",
    r"[^,]+,[^,]+",
    r"\d{1,3}(\.\d{1,3}){3}",
    r"(?i)error|warning",
    r"^(GET|POST|PUT) /\S*$",
    r"\bv\d+\.\d+\.\d+\b",
]


class TestDetection:
    @pytest.mark.parametrize("pattern", CATASTROPHIC)
    def test_a_catastrophic_pattern_is_named(self, pattern: str):
        assert catastrophic_shape(pattern), pattern

    @pytest.mark.parametrize("pattern", SAFE)
    def test_an_ordinary_pattern_is_allowed(self, pattern: str):
        assert catastrophic_shape(pattern) == "", pattern

    def test_the_reason_quotes_what_the_caller_wrote(self):
        # Not the internal placeholder form: a message showing a pattern the
        # user never typed is worse than no message.
        reason = catastrophic_shape(r"(\d+)+$")
        assert r"(\d+)" in reason

    def test_an_escaped_quantifier_is_not_a_quantifier(self):
        assert catastrophic_shape(r"(a\+)+b") == ""

    def test_a_bounded_repetition_is_allowed(self):
        assert catastrophic_shape(r"(ab{2}){3}") == ""

    def test_an_optional_group_is_allowed(self):
        assert catastrophic_shape(r"(a+)?b") == ""


class TestEnforcement:
    def test_a_catastrophic_pattern_is_refused_at_grader_construction(self):
        with pytest.raises(GraderError, match="backtrack catastrophically"):
            build_grader(GraderSpec(type="regex", params={"pattern": r"(a+)+b"}))

    def test_the_refusal_suggests_what_to_do_instead(self):
        with pytest.raises(GraderError) as caught:
            build_grader(GraderSpec(type="regex", params={"pattern": r"(a+)+b"}))
        assert "contains_all" in caught.value.remedy

    def test_a_suite_declaring_one_is_refused_at_load_time(self):
        # Before a single completion is requested, and naming the case.
        document = (
            "name: hostile\ncases:\n  - id: boom\n    prompt: p\n    graders:\n"
            '      - {type: regex, params: {pattern: "(a+)+b"}}\n'
        )
        with pytest.raises(Exception, match="boom"):
            parse_suite(document)

    def test_the_guard_runs_before_compilation_not_after(self):
        # If it ran after, this construction would already have hung.
        started = time.perf_counter()
        with pytest.raises(GraderError):
            build_grader(GraderSpec(type="regex", params={"pattern": r"(a+)+" + "b"}))
        assert time.perf_counter() - started < 1.0

    async def test_an_allowed_pattern_still_works(self):
        from aievals.graders.base import GradeContext
        from aievals.suite.models import Case

        grader = build_grader(GraderSpec(type="regex", params={"pattern": r"\bAC-\d{5}\b"}))
        result = await grader.grade("order AC-48213", Case(id="c", prompt="p"), GradeContext())
        assert result.passed

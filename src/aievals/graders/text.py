"""Graders over the response as text.

Every grader in this file is deterministic: the same response and the same
parameters give the same verdict on every machine, forever. That is what lets a
replay run treat a flipped case as a regression rather than as noise.

Two conventions hold throughout:

**Normalisation is opt-in and named.** ``case_sensitive`` and
``normalize_whitespace`` default to the strict reading. A grader that quietly
lowercases and collapses whitespace passes responses its author did not intend
to accept, and the suite silently gets weaker.

**A failure says what was missing, not that something was missing.** The detail
string names the specific value that was absent or present, because the person
reading it is looking at a red build and does not have the response in front of
them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from aievals.errors import GraderError
from aievals.graders.base import Grade, GradeContext, Grader
from aievals.graders.redos import catastrophic_shape
from aievals.graders.registry import grader

if TYPE_CHECKING:
    from aievals.suite.models import Case

#: Regular expression flags a suite may name. ``re.DEBUG`` writes to stdout and
#: is not offered; ``re.LOCALE`` depends on the machine's locale and would make
#: a suite non-reproducible, which is the one thing this package will not do.
ALLOWED_FLAGS: dict[str, re.RegexFlag] = {
    "IGNORECASE": re.IGNORECASE,
    "MULTILINE": re.MULTILINE,
    "DOTALL": re.DOTALL,
    "VERBOSE": re.VERBOSE,
    "ASCII": re.ASCII,
}

#: A pattern longer than this is not a check, it is a program.
MAX_PATTERN_LENGTH = 1000

#: How much of a response a failure message may quote. Bounded because report
#: artefacts leave the machine: a detail is meant to be the shortest thing that
#: explains the failure, not a second copy of the response.
MAX_QUOTED_RESPONSE = 80

#: How many individual values a failure message lists before summarising.
SHOWN_VALUES = 5

#: An ordering needs at least this many values before it is an ordering.
MIN_ORDERED_VALUES = 2


def _string_list(params: dict[str, Any], key: str, *, required: bool = True) -> tuple[str, ...]:
    """Read a list-of-strings parameter, refusing the shapes that mislead."""
    if key not in params:
        if required:
            raise GraderError(f"missing required parameter {key!r}.")
        return ()
    value = params[key]
    # A bare string is the mistake this catches: `values: "hello"` iterates as
    # characters, and the grader then checks for 'h', 'e', 'l', 'l', 'o' — which
    # passes on almost any response.
    if isinstance(value, str):
        raise GraderError(
            f"{key!r} must be a list of strings, not a single string. "
            f"Write '- {value}' on its own line."
        )
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GraderError(f"{key!r} must be a list of strings.")
    if not value:
        raise GraderError(f"{key!r} is empty, so this grader would pass everything.")
    return tuple(value)


def _flag(params: dict[str, Any], key: str, *, default: bool) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise GraderError(f"{key!r} must be true or false.")
    return value


def _normalise(text: str, *, case_sensitive: bool, collapse_whitespace: bool) -> str:
    if collapse_whitespace:
        text = " ".join(text.split())
    if not case_sensitive:
        text = text.casefold()
    return text


class _TextGrader(Grader):
    """Shared parameter handling for the text graders."""

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        self.case_sensitive = _flag(params, "case_sensitive", default=True)
        self.collapse_whitespace = _flag(params, "normalize_whitespace", default=False)

    def prepare(self, text: str) -> str:
        """Apply this grader's declared normalisation to *text*."""
        return _normalise(
            text,
            case_sensitive=self.case_sensitive,
            collapse_whitespace=self.collapse_whitespace,
        )


@grader("contains_all")
class ContainsAll(_TextGrader):
    """Every value in ``values`` must appear in the response.

    The score is the fraction present, so a report distinguishes "missed one of
    five" from "missed all five" while the verdict stays binary.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        self.values = _string_list(params, "values")

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Check that every required value is present."""
        haystack = self.prepare(response)
        missing = [value for value in self.values if self.prepare(value) not in haystack]
        found = len(self.values) - len(missing)
        if missing:
            shown = ", ".join(repr(value) for value in missing[:SHOWN_VALUES])
            hidden = len(missing) - SHOWN_VALUES
            more = f" (and {hidden} more)" if hidden > 0 else ""
            return self._grade(
                passed=False,
                score=found / len(self.values),
                detail=f"missing from the response: {shown}{more}",
            )
        return self._grade(passed=True, detail=f"all {found} required value(s) present")


@grader("contains_none")
class ContainsNone(_TextGrader):
    """No value in ``values`` may appear in the response.

    The house rule of most suites: refusal boilerplate, a leaked system prompt,
    a competitor's name, a phrase legal has ruled out.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        self.values = _string_list(params, "values")

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Check that no forbidden value is present."""
        haystack = self.prepare(response)
        present = [value for value in self.values if self.prepare(value) in haystack]
        if present:
            shown = ", ".join(repr(value) for value in present[:SHOWN_VALUES])
            return self._grade(
                passed=False,
                score=1.0 - len(present) / len(self.values),
                detail=f"forbidden value(s) present in the response: {shown}",
            )
        return self._grade(passed=True, detail=f"none of {len(self.values)} forbidden value(s)")


@grader("exact")
class Exact(_TextGrader):
    """The response must equal ``value``.

    ``strip`` defaults to true because trailing newlines are an artefact of the
    transport rather than a property of the answer, and a suite that fails on
    one teaches people to stop trusting the suite.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        if "value" not in params or not isinstance(params["value"], str):
            raise GraderError("'value' is required and must be a string.")
        self.value = params["value"]
        self.strip = _flag(params, "strip", default=True)

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Compare the response against the expected string."""
        actual = response.strip() if self.strip else response
        expected = self.value.strip() if self.strip else self.value
        if self.prepare(actual) == self.prepare(expected):
            return self._grade(passed=True, detail="exact match")
        return self._grade(
            passed=False,
            detail=f"expected {expected!r}, got {_clip(actual)!r}",
        )


@grader("regex")
class Regex(Grader):
    """The response must match (or not match) ``pattern``.

    The pattern comes from a suite file, which is untrusted input, so it is
    checked for catastrophic backtracking **before compilation** — see
    :mod:`aievals.graders.redos`. A timeout cannot substitute for this: Python
    offers no way to interrupt a running ``re`` match.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        pattern = params.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise GraderError("'pattern' is required and must be a non-empty string.")
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise GraderError(
                f"the pattern is {len(pattern)} characters, over the "
                f"{MAX_PATTERN_LENGTH}-character limit."
            )
        reason = catastrophic_shape(pattern)
        if reason:
            raise GraderError(
                f"the pattern can be made to backtrack catastrophically: {reason}.",
                remedy="Rewrite the repetition, or use 'contains_all' instead.",
            )
        flags = re.NOFLAG
        for name in _string_list(params, "flags", required=False):
            if name not in ALLOWED_FLAGS:
                allowed = ", ".join(sorted(ALLOWED_FLAGS))
                raise GraderError(f"unknown regex flag {name!r}. Allowed: {allowed}.")
            flags |= ALLOWED_FLAGS[name]
        try:
            self.pattern = re.compile(pattern, flags)
        except re.error as exc:
            raise GraderError(f"the pattern does not compile: {exc}.") from exc
        self.must_match = _flag(params, "must_match", default=True)

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Search the response and compare the outcome against must_match."""
        found = self.pattern.search(response)
        matched = found is not None
        if matched == self.must_match:
            where = f" at offset {found.start()}" if found else ""
            verb = "matched" if matched else "did not match"
            return self._grade(passed=True, detail=f"the pattern {verb}{where}")
        expectation = "was expected to match" if self.must_match else "was expected not to match"
        return self._grade(
            passed=False,
            detail=f"the pattern {self.pattern.pattern!r} {expectation}",
        )


@grader("ordering")
class Ordering(_TextGrader):
    """Values must appear in the response in the given order.

    For answers where sequence is the requirement: steps in a procedure, the
    caveat before the recommendation, the citation after the claim.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        self.values = _string_list(params, "values")
        if len(self.values) < MIN_ORDERED_VALUES:
            raise GraderError("'ordering' needs at least two values to order.")

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Walk the values, requiring each to appear after the last."""
        haystack = self.prepare(response)
        cursor = 0
        for position, value in enumerate(self.values):
            needle = self.prepare(value)
            index = haystack.find(needle, cursor)
            if index == -1:
                # Distinguish absent from out of order: the fixes differ.
                elsewhere = haystack.find(needle) != -1
                reason = "appears before it should" if elsewhere else "is missing"
                return self._grade(
                    passed=False,
                    score=position / len(self.values),
                    detail=f"{value!r} {reason}",
                )
            cursor = index + len(needle)
        return self._grade(passed=True, detail=f"all {len(self.values)} values in order")


def _clip(text: str, limit: int = MAX_QUOTED_RESPONSE) -> str:
    """Shorten a response for an error message."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"

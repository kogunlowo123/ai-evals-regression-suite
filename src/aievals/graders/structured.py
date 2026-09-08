"""Graders that read structure out of a response.

Models are asked for JSON, for a number, or for a set of items, and the answer
arrives wrapped in prose or a Markdown fence. These graders extract first and
judge second, because a grader that fails on ```` ```json ```` teaches its users
to loosen the check rather than fix the prompt.

The extraction is bounded and explicit — no "find something that looks like
JSON anywhere in the text" — so that what the grader accepted is legible from
the failure message.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from aievals.errors import GraderError
from aievals.graders.base import Grade, GradeContext, Grader
from aievals.graders.jsonschema import SchemaError, check_schema, validate
from aievals.graders.registry import grader

if TYPE_CHECKING:
    from aievals.suite.models import Case

#: A fenced code block, optionally tagged with a language.
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\s*\n(?P<body>.*?)```", re.DOTALL)

#: A signed decimal, with optional thousands separators and exponent. Anchored
#: on a word boundary so "v2" does not read as the number 2.
_NUMBER = re.compile(r"[-+]?\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|[-+]?\b\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

#: How many individual problems a failure message lists before summarising.
SHOWN_ERRORS = 5


def extract_json_text(response: str) -> str:
    """Return the JSON document in *response*, unchanged if it is already one.

    Three attempts, in order of confidence: the whole response, the first fenced
    block, then the span between the first opening and last closing brace or
    bracket. Anything looser starts finding JSON inside prose that merely
    contains punctuation.
    """
    stripped = response.strip()
    if stripped.startswith(("{", "[")):
        return stripped
    fence = _FENCE.search(response)
    if fence:
        return fence.group("body").strip()
    for opening, closing in (("{", "}"), ("[", "]")):
        start = response.find(opening)
        end = response.rfind(closing)
        if start != -1 and end > start:
            return response[start : end + 1]
    return stripped


@grader("json_schema")
class JsonSchema(Grader):
    """The response must parse as JSON and satisfy ``schema``.

    The schema is checked at construction against the subset in
    :mod:`aievals.graders.jsonschema`; a keyword that subset does not implement
    is an error in the suite file rather than a check that quietly does nothing.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        schema = params.get("schema")
        if not isinstance(schema, dict):
            raise GraderError("'schema' is required and must be an object.")
        try:
            check_schema(schema)
        except SchemaError as exc:
            raise GraderError(str(exc)) from exc
        self.schema = schema

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Parse the response as JSON and check it against the schema."""
        text = extract_json_text(response)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return self._grade(
                passed=False,
                detail=f"the response is not JSON: {exc.msg} at line {exc.lineno}",
            )
        errors = validate(document, self.schema)
        if errors:
            shown = "; ".join(errors[:SHOWN_ERRORS])
            hidden = len(errors) - SHOWN_ERRORS
            more = f" (and {hidden} more)" if hidden > 0 else ""
            return self._grade(passed=False, detail=f"{shown}{more}")
        return self._grade(passed=True, detail="valid against the schema")


@grader("numeric_close")
class NumericClose(Grader):
    """A number in the response must be within ``tolerance`` of ``expected``.

    ``where`` selects which number: ``first``, ``last`` or ``only`` — the last
    of which fails when the response contains more than one, and is the right
    choice when the prompt asked for a bare number and ambiguity is itself the
    defect.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        expected = params.get("expected")
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            raise GraderError("'expected' is required and must be a number.")
        tolerance = params.get("tolerance", 0.0)
        if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
            raise GraderError("'tolerance' must be a number.")
        if tolerance < 0:
            raise GraderError("'tolerance' must not be negative.")
        where = params.get("where", "first")
        if where not in {"first", "last", "only"}:
            raise GraderError("'where' must be one of: first, last, only.")
        relative = params.get("relative", False)
        if not isinstance(relative, bool):
            raise GraderError("'relative' must be true or false.")
        if relative and expected == 0:
            raise GraderError(
                "'relative' tolerance is meaningless when 'expected' is zero.",
                remedy="Use an absolute tolerance for a zero target.",
            )
        self.expected = float(expected)
        self.tolerance = float(tolerance)
        self.where = where
        self.relative = relative

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Find the selected number and compare it against the target."""
        found = [match.group(0) for match in _NUMBER.finditer(response)]
        if not found:
            return self._grade(passed=False, detail="the response contains no number")
        if self.where == "only" and len(found) > 1:
            return self._grade(
                passed=False,
                detail=f"the response contains {len(found)} numbers and 'only' was required",
            )
        chosen = found[-1] if self.where == "last" else found[0]
        actual = float(chosen.replace(",", ""))
        allowed = self.tolerance * abs(self.expected) if self.relative else self.tolerance
        difference = abs(actual - self.expected)
        if difference <= allowed:
            return self._grade(passed=True, detail=f"{actual} is within {allowed} of the target")
        kind = "relative" if self.relative else "absolute"
        return self._grade(
            passed=False,
            detail=(
                f"expected {self.expected} ± {allowed} ({kind}), got {actual}, "
                f"off by {difference:.6g}"
            ),
        )


@grader("set_overlap")
class SetOverlap(Grader):
    """A fraction of ``expected`` must appear in the response.

    Recall against a reference set, for answers where partial credit is the
    honest measure: "name the causes of X" has no single correct string, and an
    exact-match grader on such a case measures phrasing rather than knowledge.

    ``min_recall`` is the gate; the score is the recall itself, so a report
    shows the trend even while the verdict stays binary.
    """

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        super().__init__(params, required=required)
        expected = params.get("expected")
        if isinstance(expected, str):
            raise GraderError("'expected' must be a list of strings, not a single string.")
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) for item in expected)
        ):
            raise GraderError("'expected' is required and must be a non-empty list of strings.")
        recall = params.get("min_recall", 1.0)
        if not isinstance(recall, (int, float)) or isinstance(recall, bool):
            raise GraderError("'min_recall' must be a number.")
        if not 0.0 < recall <= 1.0:
            raise GraderError("'min_recall' must be in (0, 1].")
        case_sensitive = params.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise GraderError("'case_sensitive' must be true or false.")
        self.expected = tuple(expected)
        self.min_recall = float(recall)
        self.case_sensitive = case_sensitive

    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:  # noqa: ARG002
        """Measure recall against the reference set."""
        haystack = response if self.case_sensitive else response.casefold()
        present = [
            item
            for item in self.expected
            if (item if self.case_sensitive else item.casefold()) in haystack
        ]
        recall = len(present) / len(self.expected)
        if recall >= self.min_recall:
            return self._grade(
                passed=True,
                score=recall,
                detail=f"recall {recall:.2f} ≥ {self.min_recall:.2f}",
            )
        missing = [item for item in self.expected if item not in present]
        shown = ", ".join(repr(item) for item in missing[:SHOWN_ERRORS])
        hidden = len(missing) - SHOWN_ERRORS
        more = f" (and {hidden} more)" if hidden > 0 else ""
        return self._grade(
            passed=False,
            score=recall,
            detail=f"recall {recall:.2f} < {self.min_recall:.2f}; missing {shown}{more}",
        )

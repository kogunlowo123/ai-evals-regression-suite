"""What a grader is, and what it is allowed to know.

A grader turns one model response into one verdict. The interface is narrow on
purpose: a grader receives the response, the case that produced it, and a
context object — and nothing else. It has no file access, no clock, and no way
to reach the network except through the judge provider handed to it, which in
replay mode is a recording.

That narrowness is what makes a suite reproducible. A grader that could read the
environment would make the same suite produce different verdicts on two
machines, and a suite that does that is not a gate, it is a mood.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aievals.providers.base import Provider
    from aievals.suite.models import Case


@dataclass(frozen=True, slots=True)
class Grade:
    """One grader's verdict on one response.

    ``score`` is continuous in ``[0, 1]`` and ``passed`` is the verdict. They
    are separate because a partial score is useful in a report and useless in a
    gate: 0.8 does not answer "should this merge".
    """

    grader: str
    passed: bool
    score: float
    detail: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be in [0, 1], got {self.score}")

    def as_dict(self) -> dict[str, Any]:
        """Serialise for reports and baselines."""
        return {
            "grader": self.grader,
            "passed": self.passed,
            "score": round(self.score, 6),
            "detail": self.detail,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class GradeContext:
    """Everything a grader may consult beyond the response and the case.

    ``judge`` is present only when the run was configured with a judge
    provider. A model-graded grader that finds it absent fails loudly rather
    than passing by default: a check that silently becomes a no-op when its
    dependency is missing is the exact failure this repository exists to catch.
    """

    judge: Provider | None = None
    judge_model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Grader(abc.ABC):
    """Base class for graders.

    Subclasses validate their parameters **in the constructor**. The loader
    builds every grader in a suite before any completion is requested, so a
    misconfigured expectation is a load-time error naming the case rather than a
    crash forty cases into a paid run.
    """

    #: Registry name. Set by the ``@grader`` decorator.
    name: str = ""

    def __init__(self, params: dict[str, Any], *, required: bool = True) -> None:
        self.params = params
        self.required = required

    @abc.abstractmethod
    async def grade(self, response: str, case: Case, context: GradeContext) -> Grade:
        """Return a verdict on *response*."""

    def _grade(self, *, passed: bool, score: float | None = None, detail: str = "") -> Grade:
        """Build a :class:`Grade` labelled with this grader's registered name."""
        return Grade(
            grader=self.name,
            passed=passed,
            score=(1.0 if passed else 0.0) if score is None else score,
            detail=detail,
            required=self.required,
        )

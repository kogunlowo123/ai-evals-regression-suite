"""The meta-gate, which asks whether this suite discriminates at all.

An evaluation suite is a measuring instrument, and nothing in an ordinary run
checks whether the instrument works. A suite of twenty cases can report a
perfect score while every one of its graders would accept an empty string, and
the report looks identical to a suite that would catch it. This module is what
tells the two apart.

The method: take a response the suite **accepted**, corrupt it, and re-grade. If
the verdict does not flip, the suite has a hole. The mutation score is the
fraction of applicable corruptions that were caught, and a surviving mutant is
named with the case, the corruption, and what a suite ought to have noticed.

Three things about the implementation.

**The provider is not called again.** Grading is a pure function of the response
and the case, so mutation runs over the responses the base run already produced.
A meta-gate that re-ran a suite once per mutator would cost eleven times a full
run and nobody would put it in CI.

**Only cases that passed are mutated.** A case that already fails cannot be
flipped to failing, and counting it would inflate the score with cases the suite
is not measuring. For the same reason a corruption that leaves the response
byte-identical is discarded rather than scored: an unchanged "mutant" is not a
test of anything, and scoring it either way is a number about the mutator rather
than about the suite.

**Judge graders are excluded, visibly.** A model-graded check under replay has
no recording for the mutated response, so it would raise, so the mutant would be
recorded as caught — a spurious catch that flatters the score. Rather than
inflate it, judge graders are dropped from mutation grading and the report says
how many were dropped. A case whose only required grader is a judge is skipped
entirely and listed by name, because "this case is outside the meta-gate" is
something a reader has to be told rather than left to infer from a total.

This is fault injection into the graded artefact — the *response* — and not
mutation testing in the ``mutmut`` sense, which mutates the code under test.
There is no code under test here; the thing under test is the suite's power to
discriminate.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from aievals.graders.base import Grade, GradeContext
from aievals.graders.registry import build_grader
from aievals.mutation.mutators import Mutator, MutatorSet, select
from aievals.runner.result import RunResult
from aievals.suite.models import Case, GraderSpec, Suite

#: Graders excluded from mutation grading. See the module docstring.
NON_DETERMINISTIC_GRADERS = frozenset({"judge"})

DEFAULT_SEED = 20260908


@dataclass(frozen=True, slots=True)
class Mutant:
    """One corruption of one case's response, and what the suite made of it."""

    case_id: str
    mutator: str
    caught: bool
    #: Required graders that rejected the mutant. Empty when it survived.
    caught_by: tuple[str, ...] = ()
    #: What the suite should have noticed, quoted from the mutator.
    expectation: str = ""
    #: A short excerpt, for a survivor, so the reader can see what was accepted.
    excerpt: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "case_id": self.case_id,
            "mutator": self.mutator,
            "caught": self.caught,
            "caught_by": list(self.caught_by),
            "expectation": self.expectation,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True, slots=True)
class MutationReport:
    """The outcome of a meta-gate run."""

    suite_name: str
    suite_digest: str
    mutants: tuple[Mutant, ...]
    #: Cases excluded, with the reason.
    skipped: tuple[tuple[str, str], ...] = ()
    #: How many grader specifications were dropped as non-deterministic.
    judge_graders_excluded: int = 0
    seed: int = DEFAULT_SEED
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """How many applicable mutants were produced."""
        return len(self.mutants)

    @property
    def caught(self) -> int:
        """How many were rejected by the suite."""
        return sum(1 for mutant in self.mutants if mutant.caught)

    @property
    def survivors(self) -> tuple[Mutant, ...]:
        """The mutants the suite accepted. Each one is a hole."""
        return tuple(mutant for mutant in self.mutants if not mutant.caught)

    @property
    def score(self) -> float:
        """Fraction caught.

        A run that produced no applicable mutants scores **0.0**, not 1.0. An
        empty measurement is not a perfect one, and returning 1.0 here would let
        a suite pass the meta-gate by being impossible to mutate.
        """
        return self.caught / self.total if self.total else 0.0

    def by_mutator(self) -> dict[str, dict[str, int]]:
        """Caught and total per mutator, for the report table."""
        table: dict[str, dict[str, int]] = {}
        for mutant in self.mutants:
            row = table.setdefault(mutant.mutator, {"caught": 0, "total": 0})
            row["total"] += 1
            row["caught"] += int(mutant.caught)
        return dict(sorted(table.items()))

    def summary(self) -> str:
        """One line, for a terminal."""
        if not self.total:
            return "no applicable mutants were produced"
        return (
            f"{self.caught}/{self.total} mutants caught "
            f"({self.score:.1%}), {len(self.survivors)} survived"
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "suite": self.suite_name,
            "suite_digest": self.suite_digest,
            "seed": self.seed,
            "total": self.total,
            "caught": self.caught,
            "survived": len(self.survivors),
            "score": round(self.score, 6),
            "by_mutator": self.by_mutator(),
            "skipped": [{"case_id": case, "reason": reason} for case, reason in self.skipped],
            "judge_graders_excluded": self.judge_graders_excluded,
            "mutants": [mutant.as_dict() for mutant in self.mutants],
            "metadata": dict(self.metadata),
        }


class MutationHarness:
    """Runs the meta-gate over a completed run."""

    def __init__(
        self,
        *,
        mutators: tuple[Mutator, ...] | None = None,
        mutator_set: MutatorSet = "core",
        seed: int = DEFAULT_SEED,
    ) -> None:
        self.mutators = mutators if mutators is not None else select(mutator_set=mutator_set)
        self.seed = seed

    async def analyse(self, suite: Suite, run: RunResult) -> MutationReport:
        """Mutate every passing case's response and re-grade it."""
        mutants: list[Mutant] = []
        skipped: list[tuple[str, str]] = []
        excluded = 0

        for result in run.cases:
            case = suite.case_by_id(result.case_id)
            if case is None:  # pragma: no cover - the run came from this suite
                continue
            if not result.passed:
                skipped.append((case.id, "the case does not pass, so it cannot be flipped"))
                continue
            if not result.samples:  # pragma: no cover - a passing case has samples
                continue

            specs, dropped = _deterministic_graders(suite.graders_for(case))
            excluded += dropped
            if not any(spec.required for spec in specs):
                reason = (
                    "every required grader is model-graded, which cannot be "
                    "replayed against a mutated response"
                )
                skipped.append((case.id, reason))
                continue

            response = result.samples[0].response
            for mutator in self.mutators:
                if not mutator.applicable(response):
                    continue
                mutant = await self._one(case, specs, response, mutator)
                if mutant is None:
                    # The corruption left the response unchanged, so there is
                    # nothing for the suite to have caught. Counting it as a
                    # survivor would blame the suite for a mutator that did not
                    # fire; counting it as caught would inflate the score with
                    # work nobody did. It is not a mutant at all.
                    continue
                mutants.append(mutant)

        return MutationReport(
            suite_name=suite.name,
            suite_digest=run.suite_digest,
            mutants=tuple(mutants),
            skipped=tuple(skipped),
            judge_graders_excluded=excluded,
            seed=self.seed,
            metadata={"mutators": [mutator.name for mutator in self.mutators]},
        )

    async def _one(
        self,
        case: Case,
        specs: tuple[GraderSpec, ...],
        response: str,
        mutator: Mutator,
    ) -> Mutant | None:
        # A per-mutant seed derived from the case and mutator names: the same
        # pair always mutates the same way, and two different pairs do not share
        # a stream.
        # nosec B311 - a seeded PRNG is the point: the same case and mutator
        # must corrupt the response identically on every machine, forever.
        # Nothing here is a secret, a token or a nonce.
        rng = random.Random(f"{self.seed}:{case.id}:{mutator.name}")  # noqa: S311  # nosec B311
        mutated = mutator.apply(response, rng)
        if mutated == response:
            return None

        context = GradeContext()
        grades: list[Grade] = []
        for spec in specs:
            instance = build_grader(spec)
            try:
                grades.append(await instance.grade(mutated, case, context))
            except Exception as exc:  # noqa: BLE001 - a raising grader rejected it
                grades.append(
                    Grade(
                        grader=spec.type,
                        passed=False,
                        score=0.0,
                        detail=f"the grader raised: {type(exc).__name__}",
                        required=spec.required,
                    )
                )

        rejected = tuple(grade.grader for grade in grades if grade.required and not grade.passed)
        caught = bool(rejected)
        return Mutant(
            case_id=case.id,
            mutator=mutator.name,
            caught=caught,
            caught_by=rejected,
            expectation=mutator.expectation,
            excerpt="" if caught else _excerpt(mutated),
        )


def _deterministic_graders(specs: tuple[GraderSpec, ...]) -> tuple[tuple[GraderSpec, ...], int]:
    kept = tuple(spec for spec in specs if spec.type not in NON_DETERMINISTIC_GRADERS)
    return kept, len(specs) - len(kept)


def _excerpt(text: str, limit: int = 120) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "(empty)"
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"

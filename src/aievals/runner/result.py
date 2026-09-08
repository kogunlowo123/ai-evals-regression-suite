"""What a run produced.

These structures are what a baseline stores, a report renders and a gate reads,
so they are deliberately dull: plain data, no behaviour that needs a provider,
and serialisation that round-trips.

One decision is worth stating. A case run with several samples carries both
``samples`` and ``sample_passes`` rather than a single boolean, because the
difference between "5 of 5" and "3 of 5" is the difference between a correct
answer and a coin flip that happened to land well — and a structure that stores
only the verdict has thrown that away by the time anyone asks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from aievals.graders.base import Grade

#: How per-sample outcomes combine into a case verdict.
SamplePolicy = Literal["all", "majority", "any"]


@dataclass(frozen=True, slots=True)
class SampleResult:
    """One completion and the grades over it."""

    response: str
    grades: tuple[Grade, ...]
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        """True when every required grader passed and nothing errored."""
        if self.error is not None:
            return False
        return all(grade.passed for grade in self.grades if grade.required)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "response": self.response,
            "passed": self.passed,
            "grades": [grade.as_dict() for grade in self.grades],
            "latency_ms": round(self.latency_ms, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Everything one case produced, across every sample."""

    case_id: str
    samples: tuple[SampleResult, ...]
    policy: SamplePolicy = "all"
    tags: tuple[str, ...] = ()

    @property
    def sample_count(self) -> int:
        """How many completions were drawn."""
        return len(self.samples)

    @property
    def sample_passes(self) -> int:
        """How many of them passed."""
        return sum(1 for sample in self.samples if sample.passed)

    @property
    def passed(self) -> bool:
        """The case verdict, under this run's sample policy."""
        if not self.samples:
            return False
        passes = self.sample_passes
        if self.policy == "any":
            return passes > 0
        if self.policy == "majority":
            return passes * 2 > self.sample_count
        return passes == self.sample_count

    @property
    def flaky(self) -> bool:
        """True when the samples disagreed with each other.

        Reported whatever the verdict. A case that passes three times in five is
        a finding even in a run where the policy calls it a pass, because the
        next run is a coin toss and the person reading the report needs to know
        that before they trust the number above it.
        """
        return 0 < self.sample_passes < self.sample_count

    @property
    def error(self) -> str | None:
        """The first sample error, if any sample failed to complete."""
        for sample in self.samples:
            if sample.error is not None:
                return sample.error
        return None

    @property
    def latency_ms(self) -> float:
        """Mean latency across samples."""
        if not self.samples:
            return 0.0
        return sum(sample.latency_ms for sample in self.samples) / len(self.samples)

    @property
    def total_tokens(self) -> int | None:
        """Tokens across every sample, or ``None`` if any is unknown."""
        total = 0
        for sample in self.samples:
            if sample.prompt_tokens is None or sample.completion_tokens is None:
                return None
            total += sample.prompt_tokens + sample.completion_tokens
        return total

    @property
    def failing_graders(self) -> tuple[str, ...]:
        """Names of the required graders that failed, deduplicated."""
        names: list[str] = []
        for sample in self.samples:
            for grade in sample.grades:
                if grade.required and not grade.passed and grade.grader not in names:
                    names.append(grade.grader)
        return tuple(names)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "flaky": self.flaky,
            "policy": self.policy,
            "tags": list(self.tags),
            "sample_count": self.sample_count,
            "sample_passes": self.sample_passes,
            "latency_ms": round(self.latency_ms, 3),
            "total_tokens": self.total_tokens,
            "failing_graders": list(self.failing_graders),
            "error": self.error,
            "samples": [sample.as_dict() for sample in self.samples],
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    """A whole run: the cases, and the conditions they ran under."""

    suite_name: str
    suite_digest: str
    provider: str
    model: str
    cases: tuple[CaseResult, ...]
    policy: SamplePolicy = "all"
    sampled: bool = False
    hermetic: bool = True
    started_at: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """How many cases ran."""
        return len(self.cases)

    @property
    def passed(self) -> int:
        """How many cases passed."""
        return sum(1 for case in self.cases if case.passed)

    @property
    def failed(self) -> int:
        """How many cases failed."""
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        """Fraction of cases that passed; 0.0 for an empty run."""
        return self.passed / self.total if self.total else 0.0

    @property
    def flaky_cases(self) -> tuple[str, ...]:
        """Ids of cases whose samples disagreed."""
        return tuple(case.case_id for case in self.cases if case.flaky)

    @property
    def errored_cases(self) -> tuple[str, ...]:
        """Ids of cases that failed to produce a completion."""
        return tuple(case.case_id for case in self.cases if case.error is not None)

    @property
    def total_tokens(self) -> int | None:
        """Tokens across the run, or ``None`` if any case did not report them."""
        total = 0
        for case in self.cases:
            case_total = case.total_tokens
            if case_total is None:
                return None
            total += case_total
        return total

    def p95_latency_ms(self) -> float:
        """Return the 95th percentile of per-case mean latency.

        Nearest-rank, not interpolated. With twenty cases the interpolated value
        is a number that no case actually took, and a budget should be breached
        by something a reader can point at.
        """
        if not self.cases:
            return 0.0
        ordered = sorted(case.latency_ms for case in self.cases)
        rank = math.ceil(0.95 * len(ordered))
        return ordered[max(0, rank - 1)]

    def case(self, case_id: str) -> CaseResult | None:
        """Return the result for *case_id*, or ``None``."""
        for result in self.cases:
            if result.case_id == case_id:
                return result
        return None

    def outcomes(self) -> dict[str, bool]:
        """Case id to verdict, which is what the baseline comparison needs."""
        return {case.case_id: case.passed for case in self.cases}

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "suite": self.suite_name,
            "suite_digest": self.suite_digest,
            "provider": self.provider,
            "model": self.model,
            "hermetic": self.hermetic,
            "sampled": self.sampled,
            "policy": self.policy,
            "started_at": self.started_at,
            "duration_ms": round(self.duration_ms, 3),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": round(self.pass_rate, 6),
            "p95_latency_ms": round(self.p95_latency_ms(), 3),
            "total_tokens": self.total_tokens,
            "flaky_cases": list(self.flaky_cases),
            "errored_cases": list(self.errored_cases),
            "metadata": dict(self.metadata),
            "cases": [case.as_dict() for case in self.cases],
        }

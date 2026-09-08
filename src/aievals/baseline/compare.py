"""Comparing a run against a baseline.

This module reports; it does not judge. Every field below is a fact about the
two runs, and :mod:`aievals.gate` decides which of those facts fail a build.
Separating them matters because the same comparison is rendered in a report
that nobody gates on, and a comparison that has already collapsed itself into a
verdict cannot be.

The one subtlety is which cases are compared. Only the ones present on both
sides: a case added in this change has no baseline verdict, and a case removed
has no current one. Counting either as a change would put suite edits into the
regression signal, so they are reported separately, under their own names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aievals.baseline.snapshot import Baseline
from aievals.runner.result import RunResult
from aievals.stats.mcnemar import McNemarResult, mcnemar
from aievals.stats.wilson import Interval, wilson_interval


@dataclass(frozen=True, slots=True)
class Comparison:
    """What changed between a baseline and a run."""

    #: Cases that passed at baseline and fail now. The headline.
    regressions: tuple[str, ...]
    #: Cases that failed at baseline and pass now.
    improvements: tuple[str, ...]
    #: Cases in the run that the baseline does not know about.
    added: tuple[str, ...]
    #: Cases in the baseline that the run did not produce.
    removed: tuple[str, ...]
    #: True when the baseline was recorded against a different suite.
    stale: bool
    baseline_digest: str
    run_digest: str
    baseline_pass_rate: float
    run_pass_rate: float
    interval: Interval
    #: Present only when the run drew several samples per case. Under replay a
    #: flip is deterministic and a p-value would be decoration.
    test: McNemarResult | None

    @property
    def pass_rate_delta(self) -> float:
        """Change in pass rate; negative means worse."""
        return self.run_pass_rate - self.baseline_pass_rate

    @property
    def unchanged(self) -> bool:
        """True when no case changed verdict and none was added or removed."""
        return not (self.regressions or self.improvements or self.added or self.removed)

    def summary(self) -> str:
        """One line, for a terminal."""
        if self.unchanged:
            return f"no change against the baseline ({self.run_pass_rate:.1%})"
        parts = [
            f"{len(self.regressions)} regressed",
            f"{len(self.improvements)} improved",
        ]
        if self.added:
            parts.append(f"{len(self.added)} new")
        if self.removed:
            parts.append(f"{len(self.removed)} removed")
        delta = f"{self.pass_rate_delta:+.1%}"
        movement = f"{self.baseline_pass_rate:.1%} to {self.run_pass_rate:.1%}, {delta}"
        return f"{', '.join(parts)} ({movement})"

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
            "added": list(self.added),
            "removed": list(self.removed),
            "stale": self.stale,
            "baseline_digest": self.baseline_digest,
            "run_digest": self.run_digest,
            "baseline_pass_rate": round(self.baseline_pass_rate, 6),
            "run_pass_rate": round(self.run_pass_rate, 6),
            "pass_rate_delta": round(self.pass_rate_delta, 6),
            "interval": self.interval.as_dict(),
            "test": self.test.as_dict() if self.test else None,
        }


def compare(baseline: Baseline, run: RunResult, *, alpha: float = 0.05) -> Comparison:
    """Compare *run* against *baseline*."""
    before = baseline.outcomes()
    after = run.outcomes()

    shared = set(before) & set(after)
    regressions = tuple(sorted(c for c in shared if before[c] and not after[c]))
    improvements = tuple(sorted(c for c in shared if not before[c] and after[c]))

    # The statistical test is consulted only under sampling. Under replay each
    # case has one deterministic completion, so a flip is caused by the change
    # under test and a p-value over it says nothing a reader should act on.
    test = mcnemar(before, after, alpha=alpha) if run.sampled else None

    return Comparison(
        regressions=regressions,
        improvements=improvements,
        added=tuple(sorted(set(after) - set(before))),
        removed=tuple(sorted(set(before) - set(after))),
        stale=baseline.suite_digest != run.suite_digest,
        baseline_digest=baseline.suite_digest,
        run_digest=run.suite_digest,
        baseline_pass_rate=baseline.pass_rate,
        run_pass_rate=run.pass_rate,
        interval=wilson_interval(run.passed, run.total),
        test=test,
    )

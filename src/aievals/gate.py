"""The gate: the one place that turns measurements into a build verdict.

Everything else in this package reports. This module decides, and it is the
reason the project exists — an evaluation that produces a number does not stop a
bad change from merging.

## The rule that most harnesses get wrong

There are two plausible ways to fail on a regression, and using both is a bug:

* a **per-case rule** — a case that passed at baseline and fails now;
* an **aggregate rule** — the pass rate dropped by more than chance explains.

Under a single deterministic sample per case the second is *implied by* the
first. Let ``b`` be the pass→fail flips and ``c`` the fail→pass flips. The pass
rate falls exactly when ``c < b``, which requires ``b ≥ 1``, which means the
per-case rule has already fired. A gate written with both conditions has a
statistical branch that can never be the sole reason a build is red, and its
author will not find that out.

So the gate splits by **run mode**, not by adding conditions:

``replay, one sample per case``
    Deterministic by construction. A flip is a regression. No statistics —
    a p-value computed over a deterministic comparison is decoration.

``sampled``
    A flip can be noise. The paired exact McNemar test decides, over the
    discordant pairs only. Per-case flips are still *reported*, because the
    person reading a red build needs to know which cases moved, but they are
    not individually fatal.

## Ordering

Findings are emitted in a fixed order, most structural first: a stale baseline
comes before a regression, because if the baseline describes a different suite
then the regression count is not a fact about anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from aievals.baseline.compare import Comparison
from aievals.errors import EXIT_GATE_FAILED, EXIT_OK
from aievals.runner.result import RunResult
from aievals.suite.models import Suite

Severity = Literal["fail", "warn"]

#: How many case ids a finding names before it summarises the rest. A
#: finding that prints two hundred ids is one nobody reads.
LISTED = 10


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason a gate failed, or one thing worth knowing that it did not."""

    code: str
    severity: Severity
    message: str
    detail: str = ""

    def render(self) -> str:
        """Format for a terminal."""
        marker = "FAIL" if self.severity == "fail" else "warn"
        line = f"  {marker}  {self.code}: {self.message}"
        return f"{line}\n        {self.detail}" if self.detail else line

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    """The verdict, and every finding behind it."""

    findings: tuple[Finding, ...]
    run: RunResult
    comparison: Comparison | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> tuple[Finding, ...]:
        """Findings that fail the build."""
        return tuple(f for f in self.findings if f.severity == "fail")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        """Findings that do not."""
        return tuple(f for f in self.findings if f.severity == "warn")

    @property
    def passed(self) -> bool:
        """Whether the gate passed."""
        return not self.failures

    @property
    def exit_code(self) -> int:
        """The process exit code this verdict implies."""
        return EXIT_OK if self.passed else EXIT_GATE_FAILED

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "passed": self.passed,
            "findings": [finding.as_dict() for finding in self.findings],
            "run": self.run.as_dict(),
            "comparison": self.comparison.as_dict() if self.comparison else None,
            "metadata": dict(self.metadata),
        }


def evaluate(
    run: RunResult,
    suite: Suite,
    *,
    comparison: Comparison | None = None,
    allow_stale_baseline: bool = False,
    min_pass_rate: float | None = None,
) -> GateReport:
    """Apply every gate rule to *run* and return the verdict.

    *comparison* is optional: a first run has no baseline, and the threshold and
    budget rules still apply to it.
    """
    findings: list[Finding] = []

    if comparison is not None:
        findings += _baseline_findings(comparison, run, allow_stale=allow_stale_baseline)

    findings += _threshold_findings(run, suite, override=min_pass_rate)
    findings += _budget_findings(run, suite)
    findings += _health_findings(run)

    return GateReport(findings=tuple(findings), run=run, comparison=comparison)


# -- rules ---------------------------------------------------------------


def _baseline_findings(
    comparison: Comparison, run: RunResult, *, allow_stale: bool
) -> list[Finding]:
    findings: list[Finding] = []

    if comparison.stale:
        message = (
            "the baseline was recorded against a different suite, so the "
            "comparison below is not a fact about this change."
        )
        detail = (
            f"baseline {comparison.baseline_digest[:19]}… "
            f"vs suite {comparison.run_digest[:19]}…. "
            "Re-record with 'aievals baseline'."
        )
        if allow_stale:
            findings.append(Finding("STALE_BASELINE", "warn", message, detail))
        else:
            findings.append(Finding("STALE_BASELINE", "fail", message, detail))
            # Everything downstream of a stale baseline is noise. Reporting a
            # regression count derived from the wrong suite is worse than
            # reporting nothing, because it looks like a result.
            return findings

    if run.sampled:
        findings += _sampled_regression_findings(comparison)
    else:
        findings += _deterministic_regression_findings(comparison)

    if comparison.removed:
        findings.append(
            Finding(
                "CASES_REMOVED",
                "warn",
                f"{len(comparison.removed)} case(s) in the baseline did not run.",
                # Deleting a failing case raises the pass rate without improving
                # anything, so this is worth a reviewer's attention even though
                # removing a case is often legitimate.
                "Removed: " + ", ".join(comparison.removed[:LISTED]),
            )
        )
    return findings


def _deterministic_regression_findings(comparison: Comparison) -> list[Finding]:
    """Replay mode: a flip is a regression, no statistics involved."""
    if not comparison.regressions:
        return []
    shown = ", ".join(comparison.regressions[:LISTED])
    hidden = len(comparison.regressions) - LISTED
    more = f" (and {hidden} more)" if hidden > 0 else ""
    return [
        Finding(
            "REGRESSION",
            "fail",
            f"{len(comparison.regressions)} case(s) passed at baseline and fail now.",
            f"{shown}{more}",
        )
    ]


def _sampled_regression_findings(comparison: Comparison) -> list[Finding]:
    """Apply the sampled rule: the paired test decides, flips are reported anyway."""
    test = comparison.test
    findings: list[Finding] = []
    if test is None:  # pragma: no cover - compare() always sets it when sampled
        return findings

    if test.significant_regression:
        findings.append(
            Finding(
                "SIGNIFICANT_REGRESSION",
                "fail",
                f"the pass rate dropped by more than sampling explains: {test.summary()}.",
                "Regressed: " + ", ".join(comparison.regressions[:LISTED]),
            )
        )
    elif comparison.regressions:
        findings.append(
            Finding(
                "REGRESSION_NOT_SIGNIFICANT",
                "warn",
                f"{len(comparison.regressions)} case(s) flipped to failing, "
                f"which sampling can explain: {test.summary()}.",
                "Flipped: " + ", ".join(comparison.regressions[:LISTED]),
            )
        )
    return findings


def _threshold_findings(run: RunResult, suite: Suite, *, override: float | None) -> list[Finding]:
    minimum = override if override is not None else suite.thresholds.min_pass_rate
    if minimum is None:
        return []
    if run.pass_rate >= minimum:
        return []
    failing = [case.case_id for case in run.cases if not case.passed]
    return [
        Finding(
            "PASS_RATE_BELOW_MINIMUM",
            "fail",
            f"pass rate {run.pass_rate:.1%} is below the required {minimum:.1%}.",
            f"{run.failed} of {run.total} failed: " + ", ".join(failing[:LISTED]),
        )
    ]


def _budget_findings(run: RunResult, suite: Suite) -> list[Finding]:
    findings: list[Finding] = []
    thresholds = suite.thresholds

    if thresholds.max_p95_latency_ms is not None:
        observed = run.p95_latency_ms()
        if observed > thresholds.max_p95_latency_ms:
            findings.append(
                Finding(
                    "LATENCY_BUDGET_EXCEEDED",
                    "fail",
                    f"p95 latency {observed:.0f}ms is over the "
                    f"{thresholds.max_p95_latency_ms:.0f}ms budget.",
                )
            )

    if thresholds.max_total_tokens is not None:
        total = run.total_tokens
        if total is None:
            # Not silently satisfied. A provider that reports no usage cannot
            # demonstrate it stayed inside a token budget, and treating an
            # absent measurement as a passing one is how a budget stops
            # existing.
            findings.append(
                Finding(
                    "TOKEN_BUDGET_UNMEASURABLE",
                    "fail",
                    "a token budget is declared but the provider reported no usage.",
                    f"provider {run.provider!r}. Remove max_total_tokens, or use a "
                    "provider that reports token counts.",
                )
            )
        elif total > thresholds.max_total_tokens:
            findings.append(
                Finding(
                    "TOKEN_BUDGET_EXCEEDED",
                    "fail",
                    f"{total} tokens is over the {thresholds.max_total_tokens} budget.",
                )
            )
    return findings


def _health_findings(run: RunResult) -> list[Finding]:
    findings: list[Finding] = []

    if run.errored_cases:
        findings.append(
            Finding(
                "CASE_ERRORS",
                "fail",
                f"{len(run.errored_cases)} case(s) did not produce a completion.",
                # An error is not a pass and it is not a fail either: nothing
                # was measured. Failing the build is the only honest response,
                # because the alternative is a green build over an untested case.
                ", ".join(run.errored_cases[:LISTED]),
            )
        )

    if run.flaky_cases:
        findings.append(
            Finding(
                "FLAKY_CASES",
                "warn",
                f"{len(run.flaky_cases)} case(s) disagreed with themselves across samples.",
                ", ".join(run.flaky_cases[:LISTED]),
            )
        )
    return findings

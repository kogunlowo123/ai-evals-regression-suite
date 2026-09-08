"""A Markdown summary, for a pull request comment or a job summary.

Written for someone who has thirty seconds and a red build. The verdict is the
first line, the reasons are next, and the tables are below them — not the other
way round, which is how most evaluation reports are laid out and why nobody
reads past the tables.
"""

from __future__ import annotations

from aievals.gate import GateReport
from aievals.mutation.meta import MutationReport
from aievals.redaction import redact_text

#: Long lists are truncated rather than paginated: this is a summary.
LIST_LIMIT = 15


def render(report: GateReport, *, mutation: MutationReport | None = None) -> str:
    """Return a Markdown summary of *report*."""
    run = report.run
    lines: list[str] = []

    verdict = "passed" if report.passed else "FAILED"
    lines.append(f"# Evaluation gate: {verdict}")
    lines.append("")
    lines.append(
        f"`{run.suite_name}` — **{run.passed}/{run.total}** cases passed "
        f"({run.pass_rate:.1%}) against `{run.model or run.provider}`."
    )
    lines.append("")

    if report.failures:
        lines.append("## Why the build is red")
        lines.append("")
        for finding in report.failures:
            lines.append(f"- **{finding.code}** — {_clean(finding.message)}")
            if finding.detail:
                lines.append(f"  - {_clean(finding.detail)}")
        lines.append("")

    if report.warnings:
        lines.append("## Worth knowing")
        lines.append("")
        lines.extend(f"- {finding.code} — {_clean(finding.message)}" for finding in report.warnings)
        lines.append("")

    comparison = report.comparison
    if comparison is not None:
        lines.append("## Against the baseline")
        lines.append("")
        lines.append(_clean(comparison.summary()))
        lines.append("")
        lines.append(f"Pass rate: {comparison.interval.summary()}")
        if comparison.test is not None:
            lines.append("")
            lines.append(f"Paired exact McNemar test: {comparison.test.summary()}")
        lines.append("")

    failing = [case for case in run.cases if not case.passed]
    if failing:
        lines.append("## Failing cases")
        lines.append("")
        lines.append("| Case | Failed graders | Detail |")
        lines.append("| --- | --- | --- |")
        for case in failing[:LIST_LIMIT]:
            detail = _first_failure_detail(case_id=case.case_id, report=report)
            graders = ", ".join(case.failing_graders) or "—"
            lines.append(f"| `{case.case_id}` | {graders} | {_cell(detail)} |")
        if len(failing) > LIST_LIMIT:
            lines.append(f"| … | | {len(failing) - LIST_LIMIT} more |")
        lines.append("")

    if mutation is not None:
        lines += _mutation_section(mutation)

    lines.append("---")
    lines.append("")
    lines.append(
        f"Provider `{run.provider}`, hermetic: {str(run.hermetic).lower()}, "
        f"suite digest `{run.suite_digest[:19]}…`."
    )
    return "\n".join(lines) + "\n"


def _mutation_section(mutation: MutationReport) -> list[str]:
    lines = ["## Suite quality (meta-gate)", ""]
    lines.append(_clean(mutation.summary()))
    lines.append("")
    if mutation.survivors:
        lines.append("A surviving mutant is a corruption this suite accepted.")
        lines.append("")
        lines.append("| Case | Corruption | What should have been noticed |")
        lines.append("| --- | --- | --- |")
        lines.extend(
            f"| `{mutant.case_id}` | {mutant.mutator} | {_cell(mutant.expectation)} |"
            for mutant in mutation.survivors[:LIST_LIMIT]
        )
        if len(mutation.survivors) > LIST_LIMIT:
            lines.append(f"| … | | {len(mutation.survivors) - LIST_LIMIT} more |")
        lines.append("")
    else:
        table = mutation.by_mutator()
        if table:
            lines.append("| Corruption | Caught |")
            lines.append("| --- | --- |")
            lines.extend(
                f"| {name} | {row['caught']}/{row['total']} |" for name, row in table.items()
            )
            lines.append("")
    if mutation.skipped:
        lines.append(f"{len(mutation.skipped)} case(s) were outside the meta-gate.")
        lines.append("")
    return lines


def _first_failure_detail(*, case_id: str, report: GateReport) -> str:
    case = report.run.case(case_id)
    if case is None:  # pragma: no cover - the id came from the run
        return ""
    if case.error:
        return case.error
    for sample in case.samples:
        for grade in sample.grades:
            if grade.required and not grade.passed and grade.detail:
                return grade.detail
    return ""


def _clean(text: str) -> str:
    redacted, _ = redact_text(text)
    return redacted


def _cell(text: str, limit: int = 120) -> str:
    """Fit a string into a table cell without breaking the table."""
    collapsed = " ".join(_clean(text).split()).replace("|", "\\|")
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1] + "…"
    return collapsed or "—"

r"""JUnit XML, so a red evaluation looks like a red test.

The point is not the format. It is that CI systems already know how to display
this: a failing case shows up in the pull request as a named failure with a
message, next to the unit tests, rather than as a line somebody has to find in a
log. A gate people can see is a gate people act on.

Two details that are easy to get wrong and produce a file the CI silently
discards:

**Control characters are illegal in XML 1.0.** Model output contains them —
stray ``\\x00`` from a truncated stream, ``\\x1b`` from an escape sequence. They
are stripped before anything is written, because an unparseable report is worse
than a plain one.

**Findings that are not cases still need somewhere to live.** A budget breach or
a stale baseline is a gate failure with no case attached; each becomes a
synthetic test case in a ``gate`` class, so the total in CI matches the verdict.

On the ``ElementTree`` import: this module only *serialises*. Nothing here parses
a document, so the entity-expansion and external-entity attacks that make the
standard library's XML parsers a liability have no entry point, and ``defusedxml``
would be a dependency hardening a parser this package never calls.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree  # nosec B405 - serialised, never parsed

from aievals.gate import GateReport
from aievals.redaction import redact_text

#: Everything XML 1.0 forbids, except tab, newline and carriage return.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def render(report: GateReport) -> str:
    """Return the run as a JUnit XML document."""
    run = report.run
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": _clean(run.suite_name),
            "tests": str(run.total + len(report.failures)),
            "failures": str(run.failed + len(report.failures)),
            "errors": str(len(run.errored_cases)),
            "time": f"{run.duration_ms / 1000:.3f}",
            "timestamp": _clean(run.started_at),
        },
    )

    properties = ElementTree.SubElement(suite, "properties")
    for name, value in (
        ("suite_digest", run.suite_digest),
        ("provider", run.provider),
        ("model", run.model),
        ("hermetic", str(run.hermetic).lower()),
        ("pass_rate", f"{run.pass_rate:.4f}"),
    ):
        ElementTree.SubElement(properties, "property", {"name": name, "value": _clean(value)})

    for case in run.cases:
        element = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": _clean(run.suite_name),
                "name": _clean(case.case_id),
                "time": f"{case.latency_ms / 1000:.3f}",
            },
        )
        if case.error is not None:
            error = ElementTree.SubElement(
                element, "error", {"type": "CaseError", "message": _clean(case.error)}
            )
            error.text = _clean(case.error)
        elif not case.passed:
            details = "\n".join(
                f"{grade.grader}: {grade.detail}"
                for sample in case.samples
                for grade in sample.grades
                if grade.required and not grade.passed
            )
            message = f"failed: {', '.join(case.failing_graders) or 'no grader passed'}"
            failure = ElementTree.SubElement(
                element, "failure", {"type": "GraderFailure", "message": _clean(message)}
            )
            failure.text = _clean(details)

    for finding in report.failures:
        element = ElementTree.SubElement(
            suite, "testcase", {"classname": "gate", "name": _clean(finding.code), "time": "0"}
        )
        failure = ElementTree.SubElement(
            element, "failure", {"type": "GateFailure", "message": _clean(finding.message)}
        )
        failure.text = _clean(finding.detail)

    body = ElementTree.tostring(suite, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _clean(text: str) -> str:
    """Redact, then strip characters XML cannot carry."""
    redacted, _ = redact_text(text)
    return _ILLEGAL.sub("", redacted)

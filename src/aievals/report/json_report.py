"""The machine-readable report.

Written to stdout, which is why every log line in this package goes to stderr:
a report a build script cannot parse is a report nobody automates against.

**Responses are omitted by default.** A report is an artefact — uploaded to CI,
attached to a pull request, kept for months. Model output is derived from the
prompt, and evaluation prompts routinely carry customer text, support tickets
and medical questions. Embedding every response by default puts that data
somewhere nobody decided to put it. ``--include-responses`` is the deliberate
opt-in, and even then the whole document goes through redaction.

The precise claim, because a vaguer one would be false: **the full response is
replaced by its length and a digest.** A grader detail may still quote a
*bounded fragment* of the response — up to
:data:`aievals.graders.text.MAX_QUOTED_RESPONSE` characters — because the
fragment is the reason the build is red, and a report that says "the exact
grader failed" without saying what it saw sends the reader back to re-run the
suite locally. A run whose prompts carry data that must not appear at all
belongs behind a private artefact store; this flag reduces exposure, it does not
eliminate it, and SECURITY.md says so.

**Redaction runs over the assembled document, last.** Grader details quote
fragments of the response, mutation survivors carry excerpts, and error strings
carry whatever the provider said. Redacting the responses on the way in and
attaching those derived strings afterwards is precisely the leak this series
found once already.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aievals import __version__
from aievals.gate import GateReport
from aievals.mutation.meta import MutationReport
from aievals.redaction import redact_structure


def build(
    report: GateReport,
    *,
    include_responses: bool = False,
    mutation: MutationReport | None = None,
) -> dict[str, Any]:
    """Assemble the report document, redacted."""
    document: dict[str, Any] = {
        "schema": "aievals.report/1",
        "tool": f"aievals {__version__}",
        **report.as_dict(),
    }
    if mutation is not None:
        document["mutation"] = mutation.as_dict()
    if not include_responses:
        _replace_responses(document)

    cleaned, redaction = redact_structure(document)
    body = dict(cleaned)
    body["redaction"] = redaction.as_dict()
    return body


def render(
    report: GateReport,
    *,
    include_responses: bool = False,
    mutation: MutationReport | None = None,
) -> str:
    """Return the report as a JSON string."""
    document = build(report, include_responses=include_responses, mutation=mutation)
    return json.dumps(document, indent=2, ensure_ascii=False)


def _replace_responses(document: dict[str, Any]) -> None:
    """Swap each response for a length and a digest.

    A digest rather than nothing: two runs whose responses differ are visibly
    different in the report even when neither response is present, which is
    enough to tell "the model changed" from "the grader changed".
    """
    run = document.get("run")
    if not isinstance(run, dict):  # pragma: no cover - always present
        return
    for case in run.get("cases", []):
        for sample in case.get("samples", []):
            text = sample.get("response")
            if isinstance(text, str):
                sample.pop("response")
                sample["response_omitted"] = {
                    "length": len(text),
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                }
    mutation = document.get("mutation")
    if isinstance(mutation, dict):
        for mutant in mutation.get("mutants", []):
            mutant.pop("excerpt", None)

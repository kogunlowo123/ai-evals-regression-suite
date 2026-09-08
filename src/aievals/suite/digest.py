"""Content addressing for suites.

A baseline is a claim about *a particular suite*. Compare a run against a
baseline recorded from a different suite and the answer is meaningless in a way
that looks exactly like a real result — the same case ids, plausible numbers,
and a verdict nobody should act on. The digest is what makes that detectable, so
the gate can treat it as a hard failure.

Getting the composition wrong is unsound in one direction and useless in the
other, so it is spelled out rather than left to a serialiser:

**In the digest** — everything that changes what is measured. The schema
version, the suite name, the provider selection and its sampling parameters, the
thresholds, the default graders, and for every case its id, prompt, system
message and grader specifications.

**Out of the digest** — everything that does not. Prose (``description``,
``rationale``, ``describe``), tags, file paths, modification times, key
ordering, and the order of the cases themselves. Editing a comment must not
invalidate a baseline; a harness that cries wolf on prose changes trains people
to pass ``--allow-stale-baseline``, and then the check is gone.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aievals.suite.models import Case, GraderSpec, Suite

#: Bumped when the composition below changes in a way that must invalidate
#: existing baselines. Part of the hashed payload, so the change propagates.
DIGEST_VERSION = 1


def _grader_payload(spec: GraderSpec) -> dict[str, Any]:
    # `describe` is prose for a report and is deliberately absent.
    return {"type": spec.type, "params": spec.params, "required": spec.required}


def _case_payload(case: Case) -> dict[str, Any]:
    # `tags` and `rationale` are absent: neither changes what is asked or what
    # counts as a correct answer.
    return {
        "id": case.id,
        "prompt": case.prompt,
        "system": case.system,
        "graders": [_grader_payload(spec) for spec in case.graders],
    }


def digest_payload(suite: Suite) -> dict[str, Any]:
    """Return the exact structure that is hashed.

    Exposed because a digest whose inputs cannot be inspected is impossible to
    debug: when a baseline goes stale unexpectedly, the answer is a diff of two
    payloads, not a pair of hex strings.
    """
    return {
        "digest_version": DIGEST_VERSION,
        "schema_version": suite.schema_version,
        "name": suite.name,
        "provider": {
            "name": suite.provider.name,
            "model": suite.provider.model,
            "temperature": suite.provider.temperature,
            "max_tokens": suite.provider.max_tokens,
            "options": suite.provider.options,
        },
        "thresholds": suite.thresholds.model_dump(mode="json"),
        "default_graders": [_grader_payload(spec) for spec in suite.default_graders],
        # Sorted by id: reordering cases in a file does not change the suite.
        "cases": [_case_payload(case) for case in sorted(suite.cases, key=lambda c: c.id)],
    }


def canonical_json(payload: Any) -> str:
    """Serialise deterministically: sorted keys, no incidental whitespace.

    ``ensure_ascii`` stays on so the bytes hashed do not depend on the encoding
    of the file the payload was read from.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def suite_digest(suite: Suite) -> str:
    """Return the suite's content address, as ``sha256:<hex>``."""
    encoded = canonical_json(digest_payload(suite)).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

"""The baseline: what this suite did last time, committed to the repository.

A baseline is small on purpose. It records the *verdicts*, not the responses.
Storing model output would make the file enormous, put whatever the model said
into version control, and tempt someone to diff prose — none of which helps
decide whether a change is a regression.

It carries the suite digest, and that is the field the gate cares about most.
A baseline recorded against a different suite produces a comparison that looks
entirely normal — same case ids, plausible numbers, a verdict — and means
nothing. See :mod:`aievals.suite.digest`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aievals import __version__
from aievals.errors import BaselineError
from aievals.runner.result import RunResult

BASELINE_VERSION = 1


class Baseline:
    """A recorded set of per-case verdicts for one suite."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    # -- construction ----------------------------------------------------

    @classmethod
    def from_run(cls, run: RunResult, *, note: str = "") -> Baseline:
        """Snapshot *run*."""
        cases = {
            case.case_id: {
                "passed": case.passed,
                "sample_passes": case.sample_passes,
                "sample_count": case.sample_count,
                "failing_graders": list(case.failing_graders),
            }
            for case in run.cases
        }
        return cls(
            {
                "baseline_version": BASELINE_VERSION,
                "recorded_with": f"aievals {__version__}",
                "recorded_at": datetime.now(UTC).isoformat(),
                "note": note,
                "suite": run.suite_name,
                "suite_digest": run.suite_digest,
                "provider": run.provider,
                "model": run.model,
                "sampled": run.sampled,
                "policy": run.policy,
                "hermetic": run.hermetic,
                "total": run.total,
                "passed": run.passed,
                "pass_rate": round(run.pass_rate, 6),
                "cases": dict(sorted(cases.items())),
            }
        )

    @classmethod
    def load(cls, path: str | Path) -> Baseline:
        """Read a baseline from *path*."""
        file = Path(path)
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BaselineError(
                f"there is no baseline at {file}.",
                remedy="Record one with 'aievals baseline --suite <suite> --out <path>'.",
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BaselineError(f"{file.name} could not be read as JSON: {exc}.") from exc

        if not isinstance(payload, dict):
            raise BaselineError(f"{file.name} is not a baseline: the top level is not an object.")
        version = payload.get("baseline_version")
        if version != BASELINE_VERSION:
            raise BaselineError(
                f"{file.name} is baseline version {version!r}; this build reads "
                f"version {BASELINE_VERSION}.",
                remedy="Re-record the baseline.",
            )
        if not isinstance(payload.get("cases"), dict):
            raise BaselineError(f"{file.name} has no 'cases' object.")
        if not isinstance(payload.get("suite_digest"), str):
            raise BaselineError(
                f"{file.name} has no suite digest, so it cannot be checked against a suite.",
                remedy="Re-record the baseline.",
            )
        return cls(payload)

    # -- accessors -------------------------------------------------------

    @property
    def suite_name(self) -> str:
        """The suite this baseline was recorded from."""
        return str(self._payload.get("suite", ""))

    @property
    def suite_digest(self) -> str:
        """The content address of that suite."""
        return str(self._payload["suite_digest"])

    @property
    def sampled(self) -> bool:
        """Whether the recorded run drew more than one sample per case."""
        return bool(self._payload.get("sampled", False))

    @property
    def pass_rate(self) -> float:
        """The recorded pass rate."""
        return float(self._payload.get("pass_rate", 0.0))

    @property
    def recorded_at(self) -> str:
        """When it was recorded, ISO 8601."""
        return str(self._payload.get("recorded_at", ""))

    def outcomes(self) -> dict[str, bool]:
        """Case id to recorded verdict."""
        cases = self._payload["cases"]
        return {str(key): bool(value.get("passed", False)) for key, value in cases.items()}

    def as_dict(self) -> dict[str, Any]:
        """Return a copy of the payload."""
        return dict(self._payload)

    def save(self, path: str | Path) -> Path:
        """Write this baseline to *path* and return the path.

        No redaction pass: a baseline holds case ids, verdicts and grader names,
        all of which come from the suite file rather than from model output.
        Adding responses to this file would change that, which is one more
        reason not to.
        """
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(
            json.dumps(self._payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return file

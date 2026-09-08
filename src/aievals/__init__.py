"""An LLM evaluation harness whose evaluations are tests.

The distinction this package exists for: an evaluation that prints a score is a
dashboard, and a dashboard does not stop a bad change from merging. Everything
here is arranged so that a regression *fails a build*.

Three properties, each enforced by code rather than described in a README:

``a suite that cannot fail is not a suite``
    :mod:`aievals.mutation` corrupts model responses and asserts the suite
    notices. A suite that scores well on real output but survives a truncated,
    refused or contradicted response was never measuring anything.

``a score is not a verdict``
    In replay mode a comparison is deterministic, so a case that flips is a
    regression, full stop. Under sampling, :mod:`aievals.stats` applies an exact
    McNemar test over the discordant pairs, because the two runs are paired and
    treating them as independent samples is the wrong test.

``an evaluation you cannot re-run is an anecdote``
    Suites are content-addressed, providers are recorded, and a baseline
    recorded against a different suite digest is a hard failure rather than a
    warning: comparing against the wrong thing is worse than not comparing.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

"""The exception hierarchy, and the exit codes the command line maps it to.

Every error carries a ``remedy``: the message says what is wrong, the remedy
says what to do about it. A tool that reports "invalid suite" and stops has told
the user only that they must now read the source.

Exit codes are part of the interface. A CI job distinguishes "the gate found a
regression" (2) from "the harness could not run" (3), because those call for
different responses and a single non-zero code makes them indistinguishable.
"""

from __future__ import annotations

#: The run completed and every gate passed.
EXIT_OK = 0
#: Reserved for the command line's own usage errors, which argparse emits.
EXIT_USAGE = 1
#: A gate failed: a regression, a threshold breach, or a surviving mutant. The
#: harness worked correctly; the thing it measured is bad.
EXIT_GATE_FAILED = 2
#: The harness could not do its job: an unreadable suite, a missing cassette, a
#: provider error. Nothing was measured, so nothing was proven either way.
EXIT_ERROR = 3


class AievalsError(Exception):
    """Base class. Carries a remedy alongside the message."""

    def __init__(self, message: str, *, remedy: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def render(self) -> str:
        """Format for a terminal: the problem, then what to do about it."""
        if self.remedy:
            return f"{self.message}\n  {self.remedy}"
        return self.message


class SuiteError(AievalsError):
    """A suite file could not be loaded, parsed or validated."""


class GraderError(AievalsError):
    """A grader specification is unusable, or a grader refused to run."""


class ProviderError(AievalsError):
    """A model provider could not produce a completion."""


class RetryableProviderError(ProviderError):
    """A provider failure that a second attempt might not hit.

    A separate class rather than a flag on :class:`ProviderError` so the
    retry loop is an ``except`` clause. A boolean attribute set at some raise
    sites and not others is the shape where a new error path silently becomes
    non-retryable — or worse, retries a 401 three times.
    """


class HermeticViolationError(ProviderError):
    """A provider that reaches the network was constructed in hermetic mode.

    Raised at construction, not at request time. A guard that fires when the
    socket opens has already let a caller build an object whose whole purpose is
    forbidden, and the failure then depends on whether a code path happened to
    be taken.
    """


class CassetteError(ProviderError):
    """A recording is missing, malformed, or does not match the request."""


class BaselineError(AievalsError):
    """A baseline is missing, malformed, or describes a different suite."""


class ConfigError(AievalsError):
    """An environment variable names a setting this tool does not have."""

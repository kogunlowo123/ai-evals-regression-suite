"""Suite definitions: the cases, their expectations, and the gates over them."""

from __future__ import annotations

from aievals.suite.digest import suite_digest
from aievals.suite.loader import load_suite, parse_suite
from aievals.suite.models import Case, GraderSpec, ProviderSpec, Suite, Thresholds

__all__ = [
    "Case",
    "GraderSpec",
    "ProviderSpec",
    "Suite",
    "Thresholds",
    "load_suite",
    "parse_suite",
    "suite_digest",
]

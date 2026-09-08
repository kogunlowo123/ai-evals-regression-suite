"""Graders: the things that turn a response into a verdict.

Importing this package registers every built-in grader. The imports at the
bottom exist for that side effect.

The registry is a security boundary, not a convenience. A suite file names a
grader; it cannot supply one. See :mod:`aievals.graders.registry`.
"""

from __future__ import annotations

from aievals.graders.base import Grade, GradeContext, Grader
from aievals.graders.registry import (
    build_grader,
    grader,
    grader_class,
    is_registered,
    registered_names,
)

# Registration side effects.
from aievals.graders import judge as _judge  # noqa: F401  isort:skip
from aievals.graders import structured as _structured  # noqa: F401  isort:skip
from aievals.graders import text as _text  # noqa: F401  isort:skip

__all__ = [
    "Grade",
    "GradeContext",
    "Grader",
    "build_grader",
    "grader",
    "grader_class",
    "is_registered",
    "registered_names",
]

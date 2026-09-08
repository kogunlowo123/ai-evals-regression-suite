"""The grader registry.

This module is the reason a suite file cannot execute code. A suite names a
grader with a short string; the string is looked up here; nothing is imported,
resolved or evaluated from suite data. Adding a grader is a Python change that
goes through review, which is the point.

Registration is explicit and duplicate names are rejected. A second registration
under an existing name would silently change what every suite using that name
checks — the kind of change that makes a gate quietly weaker while every report
still says "passed".
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from aievals.errors import GraderError
from aievals.graders.base import Grader

if TYPE_CHECKING:
    from aievals.suite.models import GraderSpec

_REGISTRY: dict[str, type[Grader]] = {}

G = TypeVar("G", bound=Grader)


def grader(name: str) -> Callable[[type[G]], type[G]]:
    """Register a grader class under *name*."""

    def register(cls: type[G]) -> type[G]:
        if name in _REGISTRY:
            raise GraderError(
                f"grader {name!r} is already registered by "
                f"{_REGISTRY[name].__module__}.{_REGISTRY[name].__name__}.",
                remedy="Pick a different name; re-registering silently changes "
                "what every existing suite means.",
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return register


def is_registered(name: str) -> bool:
    """Return whether *name* resolves to a grader."""
    return name in _REGISTRY


def registered_names() -> tuple[str, ...]:
    """Return every registered grader name, sorted."""
    return tuple(sorted(_REGISTRY))


def grader_class(name: str) -> type[Grader]:
    """Return the class registered under *name*."""
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(registered_names())
        raise GraderError(
            f"no grader named {name!r}.",
            remedy=f"Known graders: {known}.",
        ) from None


def build_grader(spec: GraderSpec) -> Grader:
    """Construct the grader described by *spec*.

    Parameter validation happens in the constructor, so a failure here is a
    failure in the suite file rather than in a run.
    """
    return grader_class(spec.type)(dict(spec.params), required=spec.required)

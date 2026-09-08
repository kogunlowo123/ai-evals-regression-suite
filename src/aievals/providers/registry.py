"""The provider registry, and hermetic mode.

Same shape as the grader registry — names in, classes out, nothing imported
from data — with one addition: :func:`build_provider` refuses to construct a
network-reaching provider when ``hermetic`` is set.

Hermetic mode is the default for ``aievals gate``. A gate that could reach a
vendor is a gate whose verdict depends on that vendor's availability, its
current model weights, and a rate limiter — none of which are properties of the
change being tested.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from aievals.errors import HermeticViolationError, ProviderError
from aievals.providers.base import Provider

if TYPE_CHECKING:
    from aievals.suite.models import ProviderSpec

_REGISTRY: dict[str, type[Provider]] = {}

P = TypeVar("P", bound=Provider)


def provider(name: str) -> Callable[[type[P]], type[P]]:
    """Register a provider class under *name*."""

    def register(cls: type[P]) -> type[P]:
        if name in _REGISTRY:
            raise ProviderError(f"provider {name!r} is already registered.")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return register


def is_registered(name: str) -> bool:
    """Return whether *name* resolves to a provider."""
    return name in _REGISTRY


def registered_names() -> tuple[str, ...]:
    """Return every registered provider name, sorted."""
    return tuple(sorted(_REGISTRY))


def provider_class(name: str) -> type[Provider]:
    """Return the class registered under *name*."""
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(registered_names())
        raise ProviderError(
            f"no provider named {name!r}.",
            remedy=f"Known providers: {known}.",
        ) from None


def build_provider(
    spec: ProviderSpec,
    *,
    hermetic: bool = True,
    extra: dict[str, Any] | None = None,
) -> Provider:
    """Construct the provider described by *spec*.

    Raises :class:`~aievals.errors.HermeticViolationError` before constructing
    anything when *hermetic* is set and the provider can reach the network.
    """
    cls = provider_class(spec.name)
    if hermetic and cls.reaches_network:
        raise HermeticViolationError(
            f"provider {spec.name!r} can reach the network and this run is hermetic.",
            remedy=(
                "Record cassettes once with 'aievals record', commit them, and run "
                "the gate against 'replay'. Pass --allow-network to opt out, which "
                "makes the run's verdict depend on a third party."
            ),
        )
    options = {**spec.options, **(extra or {})}
    return cls(options)

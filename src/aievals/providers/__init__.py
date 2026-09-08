"""Model providers.

Importing this package registers every built-in provider. The imports at the
bottom exist for that side effect, which is why they are there and not removed
as unused.
"""

from __future__ import annotations

from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.providers.cassette import Cassette
from aievals.providers.registry import (
    build_provider,
    is_registered,
    provider,
    provider_class,
    registered_names,
)

# Registration side effects. Ordered so the offline providers are registered
# first: if the network-facing module ever fails to import, a hermetic run still
# works, which is the mode that must never depend on the other one.
from aievals.providers import offline as _offline  # noqa: F401  isort:skip
from aievals.providers import openai_compat as _openai_compat  # noqa: F401  isort:skip

__all__ = [
    "Cassette",
    "Completion",
    "CompletionRequest",
    "Provider",
    "build_provider",
    "is_registered",
    "provider",
    "provider_class",
    "registered_names",
]

"""The model interface, and the one flag that makes hermetic mode real.

Every provider declares :attr:`Provider.reaches_network`. The registry refuses
to construct a provider with that flag set when a run is hermetic, and it
refuses at **construction**, not at request time — a guard that fires when the
socket opens has already allowed a caller to build the forbidden object, and
whether it then fires depends on which code path a run happens to take.

That is the whole enforcement of "this evaluation did not talk to a vendor". It
is one boolean and one check, which is why it is stated here rather than in a
paragraph of SECURITY.md.
"""

from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass
from typing import Any

from aievals.suite.digest import canonical_json


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One request for a completion.

    Frozen and fully explicit because its fields are the cassette key: any
    property of a request that can change the answer has to be in here, or a
    replay returns a recording made under different conditions.
    """

    prompt: str
    model: str
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024

    def fingerprint(self) -> str:
        """Return the content address of this request, as ``sha256:<hex>``."""
        payload = {
            "prompt": self.prompt,
            "model": self.model,
            "system": self.system,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        encoded = canonical_json(payload).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class Completion:
    """One model response, and what it cost.

    Token counts are ``None`` rather than zero when a provider does not report
    them. Zero would be a measurement, and a budget silently satisfied by a
    provider that counts nothing is a budget that is not enforced.
    """

    text: str
    model: str
    provider: str
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str = "stop"
    #: True when the text came from a recording rather than a live model.
    replayed: bool = False

    @property
    def total_tokens(self) -> int | None:
        """Prompt plus completion tokens, or ``None`` if either is unknown."""
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, Any]:
        """Serialise for reports and cassettes."""
        return {
            "text": self.text,
            "model": self.model,
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason,
            "replayed": self.replayed,
        }


class Provider(abc.ABC):
    """Something that turns a request into a completion."""

    #: Registry name, set by the ``@provider`` decorator.
    name: str = ""

    #: Whether this provider can open a network connection. Consulted by the
    #: registry before construction in hermetic mode. Defaults to ``True`` so a
    #: new provider that forgets to declare it is refused rather than admitted:
    #: the safe default for a security flag is the restrictive one.
    reaches_network: bool = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = dict(options or {})

    @abc.abstractmethod
    async def complete(self, request: CompletionRequest) -> Completion:
        """Return a completion for *request*."""

    async def aclose(self) -> None:
        """Release any resources. Safe to call more than once."""
        return

    def describe(self) -> str:
        """Return a one-line summary for ``aievals doctor``."""
        egress = "may reach the network" if self.reaches_network else "no egress"
        return f"{self.name} ({egress})"

"""Providers that cannot reach the network.

Three of them, and the distinction between the first two matters:

``replay``
    Serves recorded completions. The provider a gate runs against. A miss is an
    error, never a live call.

``scripted``
    Serves completions from an in-memory table supplied by the caller. For
    tests and for :mod:`aievals.mutation`, which needs to answer with a
    deliberately corrupted response and cannot do that through a file.

``echo``
    Returns the prompt. Useless for evaluating a model and exactly right for
    checking that the harness itself moves text end to end — the smallest
    provider that makes ``aievals doctor`` meaningful without any recording.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aievals.errors import ProviderError
from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.providers.cassette import Cassette
from aievals.providers.registry import provider


@provider("replay")
class ReplayProvider(Provider):
    """Serves completions from a cassette.

    ``options`` takes ``cassette``: a path, or an already-loaded
    :class:`~aievals.providers.cassette.Cassette`. The second form is what the
    runner uses so a suite's cassette is read once rather than once per case.
    """

    reaches_network = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        source = self.options.get("cassette")
        if isinstance(source, Cassette):
            self._cassette = source
        elif isinstance(source, (str, Path)):
            self._cassette = Cassette.load(source)
        elif source is None:
            raise ProviderError(
                "the replay provider needs a cassette.",
                remedy="Pass --cassette, or set provider.options.cassette in the suite.",
            )
        else:
            raise ProviderError(f"'cassette' must be a path, not {type(source).__name__}.")

    async def complete(self, request: CompletionRequest) -> Completion:
        """Return the recorded completion, or raise."""
        return self._cassette.get(request)

    def describe(self) -> str:
        """Report how many recordings are loaded."""
        return f"replay (no egress, {len(self._cassette)} recording(s))"


@provider("scripted")
class ScriptedProvider(Provider):
    """Serves completions from a table or a callable.

    ``options`` takes exactly one of:

    ``responses``
        A mapping of case prompt to response text, or a list consumed in order.
    ``respond``
        A callable taking a :class:`CompletionRequest` and returning a string.
        This is the hook :mod:`aievals.mutation` uses to corrupt a response
        without touching a file.
    """

    reaches_network = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._respond: Callable[[CompletionRequest], str] | None = None
        self._table: dict[str, str] | None = None
        self._queue: list[str] | None = None

        respond = self.options.get("respond")
        responses = self.options.get("responses")
        if respond is not None and responses is not None:
            raise ProviderError("pass 'respond' or 'responses', not both.")
        if callable(respond):
            self._respond = respond
        elif isinstance(responses, dict):
            self._table = {str(key): str(value) for key, value in responses.items()}
        elif isinstance(responses, list):
            self._queue = [str(item) for item in responses]
        else:
            raise ProviderError(
                "the scripted provider needs 'respond' (a callable) or "
                "'responses' (a mapping or a list)."
            )

    async def complete(self, request: CompletionRequest) -> Completion:
        """Return the scripted answer for *request*."""
        started = time.perf_counter()
        if self._respond is not None:
            text = self._respond(request)
        elif self._table is not None:
            if request.prompt not in self._table:
                raise ProviderError(
                    f"the scripted provider has no response for {request.prompt[:60]!r}."
                )
            text = self._table[request.prompt]
        elif self._queue is not None:
            # Not an assert: `assert` is removed under `-O`, so a guard written
            # that way is a guard that is absent in exactly the deployment where
            # a stray None would be hardest to diagnose.
            if not self._queue:
                raise ProviderError("the scripted provider ran out of queued responses.")
            text = self._queue.pop(0)
        else:  # pragma: no cover - the constructor sets exactly one of the three
            raise ProviderError("the scripted provider has nothing to answer with.")
        return Completion(
            text=text,
            model=request.model or "scripted",
            provider=self.name,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


@provider("echo")
class EchoProvider(Provider):
    """Returns the prompt, optionally with a prefix.

    Not a model. It exists so ``aievals doctor`` can prove the harness runs a
    case end to end on a machine with no recordings and no credentials.
    """

    reaches_network = False

    async def complete(self, request: CompletionRequest) -> Completion:
        """Return the prompt back."""
        prefix = str(self.options.get("prefix", ""))
        return Completion(
            text=prefix + request.prompt,
            model=request.model or "echo",
            provider=self.name,
            latency_ms=0.0,
            prompt_tokens=len(request.prompt.split()),
            completion_tokens=len(request.prompt.split()),
        )

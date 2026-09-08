"""Recorded completions.

A cassette is the artefact that makes an evaluation re-runnable. It maps a
request fingerprint to the completion a real model gave, so a gate can compare
today's code against yesterday's baseline without either run depending on a
vendor being up, a model being unchanged, or a card being charged.

Two properties are load-bearing:

**The key is a content address of the request.** Model, prompt, system message,
temperature and token limit. Change any of them and the recording no longer
applies — which is correct, and is why a missing entry is an error rather than a
fallback to a live call. A replay that quietly reaches the network when it
misses is a hermetic run that is not hermetic.

**Redaction runs over the assembled cassette, immediately before it is
written.** Cassettes are committed to repositories. A completion that quotes a
key from a fixture, or a prompt built from an environment variable, would
otherwise be a credential in version control — the one outcome this whole series
of projects treats as unacceptable. The fingerprint is computed from the
*original* request, so redacting the stored copy does not break lookup.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aievals import __version__
from aievals.errors import CassetteError
from aievals.providers.base import Completion, CompletionRequest
from aievals.redaction import redact_structure

CASSETTE_VERSION = 1

#: Cassettes are committed, reviewed, and occasionally enormous by accident.
MAX_CASSETTE_BYTES = 64 * 1024 * 1024


class Cassette:
    """A collection of recorded completions, keyed by request fingerprint."""

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self._entries: dict[str, dict[str, Any]] = dict(entries or {})

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, fingerprint: str) -> bool:
        return fingerprint in self._entries

    @property
    def fingerprints(self) -> tuple[str, ...]:
        """Every recorded fingerprint, in insertion order."""
        return tuple(self._entries)

    def put(self, request: CompletionRequest, completion: Completion) -> None:
        """Record *completion* as the answer to *request*."""
        self._entries[request.fingerprint()] = {
            "request": {
                "model": request.model,
                "system": request.system,
                "prompt": request.prompt,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            "completion": completion.as_dict(),
            "recorded_at": datetime.now(UTC).isoformat(),
        }

    def get(self, request: CompletionRequest) -> Completion:
        """Return the recorded completion for *request*.

        Raises rather than falling back. The whole value of replay is that a
        miss is visible.
        """
        fingerprint = request.fingerprint()
        entry = self._entries.get(fingerprint)
        if entry is None:
            raise CassetteError(
                f"no recording for this request ({fingerprint[:19]}…), model {request.model!r}.",
                remedy=(
                    "Re-record with 'aievals record --suite <suite> --allow-network'. "
                    "A prompt, model, temperature or token-limit change invalidates "
                    "the old recording, which is the point."
                ),
            )
        payload = dict(entry["completion"])
        return Completion(
            text=str(payload.get("text", "")),
            model=str(payload.get("model", request.model)),
            provider=str(payload.get("provider", "replay")),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            prompt_tokens=_optional_int(payload.get("prompt_tokens")),
            completion_tokens=_optional_int(payload.get("completion_tokens")),
            finish_reason=str(payload.get("finish_reason", "stop")),
            replayed=True,
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the redacted, writable form of this cassette."""
        body: dict[str, Any] = {
            "cassette_version": CASSETTE_VERSION,
            "recorded_with": f"aievals {__version__}",
            "entries": [
                {"fingerprint": fingerprint, **entry}
                for fingerprint, entry in self._entries.items()
            ],
        }
        # Assembled first, redacted once. See the module docstring.
        cleaned, redaction = redact_structure(body)
        cleaned["redaction"] = redaction.as_dict()
        return dict(cleaned)

    def save(self, path: str | Path) -> Path:
        """Write this cassette to *path*, redacted, and return the path."""
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_payload(), indent=2, sort_keys=False, ensure_ascii=False)
        file.write_text(text + "\n", encoding="utf-8", newline="\n")
        return file

    @classmethod
    def load(cls, path: str | Path) -> Cassette:
        """Read a cassette from *path*."""
        file = Path(path)
        try:
            size = file.stat().st_size
        except OSError as exc:
            raise CassetteError(
                f"{file} could not be opened: {exc.strerror or exc}.",
                remedy="Record it with 'aievals record', or point --cassette elsewhere.",
            ) from exc
        if size > MAX_CASSETTE_BYTES:
            raise CassetteError(
                f"{file.name} is {size} bytes, over the {MAX_CASSETTE_BYTES}-byte limit."
            )
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CassetteError(f"{file.name} could not be read as JSON: {exc}.") from exc

        if not isinstance(payload, dict):
            raise CassetteError(f"{file.name} is not a cassette: the top level is not an object.")
        version = payload.get("cassette_version")
        if version != CASSETTE_VERSION:
            raise CassetteError(
                f"{file.name} is cassette version {version!r}, and this build "
                f"reads version {CASSETTE_VERSION}.",
                remedy="Re-record it.",
            )
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise CassetteError(f"{file.name} has no 'entries' list.")

        entries: dict[str, dict[str, Any]] = {}
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict) or "fingerprint" not in entry:
                raise CassetteError(f"{file.name}: entry {index} has no fingerprint.")
            if not isinstance(entry.get("completion"), dict):
                raise CassetteError(f"{file.name}: entry {index} has no completion.")
            entries[str(entry["fingerprint"])] = entry
        return cls(entries)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None

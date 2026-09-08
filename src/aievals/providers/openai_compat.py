"""The one provider that opens a socket.

It speaks the OpenAI ``/v1/chat/completions` shape, which is what almost every
gateway, local runtime and hosted vendor exposes. It is the only network-facing
code in the package, so it is the only place a reader has to audit for egress.

**A credential never appears in a suite file.** The suite names an *environment
variable*; this provider reads it. The convenient design — ``api_key: sk-…`` in
YAML — puts a live credential in a file whose whole purpose is to be committed
and shared, and no amount of documentation undoes that. There is no code path
here that accepts a key as a literal.

**It is exercised in CI, against a loopback server.** Request construction,
authentication headers, timeouts, retry and backoff, error mapping and usage
parsing all run under test against a fake HTTP server on ``127.0.0.1``. What CI
never does is talk to a vendor. A provider that is never exercised is untested
code in the one security-sensitive spot in the package, which is exactly the
mistake this series has already made once.

``urllib.request`` rather than a client library: the dependency would exist to
save twenty lines in a component most users never construct.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from aievals.errors import ProviderError, RetryableProviderError
from aievals.logging import get_logger
from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.providers.registry import provider

logger = get_logger(__name__)

#: Only these schemes. A ``file://`` base URL would turn this provider into a
#: file reader driven by a suite file, which is a very different thing.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Status codes worth trying again. A 4xx that is not 429 will not change.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@provider("openai_compatible")
class OpenAICompatibleProvider(Provider):
    """Chat completions over HTTP.

    Options:

    ``base_url``
        Root of the API, for example ``https://api.openai.com/v1``. Falls back
        to ``AIEVALS_BASE_URL``.
    ``api_key_env``
        Name of the environment variable holding the credential. Defaults to
        ``OPENAI_API_KEY``. Never the credential itself.
    ``timeout_s``, ``attempts``
        Per-request timeout and retry count.
    ``headers``
        Extra headers, for gateways that need a routing or tenant header.
    """

    reaches_network = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)

        base_url = str(self.options.get("base_url") or os.environ.get("AIEVALS_BASE_URL") or "")
        if not base_url:
            raise ProviderError(
                "the openai_compatible provider needs a base URL.",
                remedy="Set provider.options.base_url in the suite, or AIEVALS_BASE_URL.",
            )
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ALLOWED_SCHEMES:
            raise ProviderError(
                f"{parsed.scheme or '(none)'} is not an allowed URL scheme.",
                remedy=f"Use one of: {', '.join(sorted(ALLOWED_SCHEMES))}.",
            )
        if not parsed.netloc:
            raise ProviderError(f"{base_url!r} has no host.")
        self.base_url = base_url.rstrip("/")

        key_variable = str(self.options.get("api_key_env", "OPENAI_API_KEY"))
        if "api_key" in self.options:
            raise ProviderError(
                "'api_key' is not accepted here.",
                remedy=(
                    "Suite files are committed. Set 'api_key_env' to the name of an "
                    "environment variable holding the credential instead."
                ),
            )
        self._api_key = os.environ.get(key_variable, "")
        self._key_variable = key_variable

        self.timeout_s = float(self.options.get("timeout_s", DEFAULT_TIMEOUT_S))
        self.attempts = int(self.options.get("attempts", DEFAULT_ATTEMPTS))
        if self.attempts < 1:
            raise ProviderError("'attempts' must be at least 1.")
        extra = self.options.get("headers", {})
        if not isinstance(extra, dict):
            raise ProviderError("'headers' must be a mapping.")
        self.extra_headers = {str(k): str(v) for k, v in extra.items()}

    async def complete(self, request: CompletionRequest) -> Completion:
        """Send *request* and return the model's answer."""
        if not self._api_key:
            raise ProviderError(
                f"the environment variable {self._key_variable} is not set.",
                remedy=f"Export {self._key_variable}, or use the replay provider.",
            )
        body = self._build_body(request)
        started = time.perf_counter()
        payload = await self._post_with_retries(body)
        elapsed = (time.perf_counter() - started) * 1000
        return self._to_completion(payload, request, elapsed)

    def _build_body(self, request: CompletionRequest) -> bytes:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        document = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            # Streaming would give nothing here: a grader needs the whole
            # response before it can say anything, and the added parsing is
            # surface for no benefit.
            "stream": False,
        }
        return json.dumps(document).encode("utf-8")

    async def _post_with_retries(self, body: bytes) -> dict[str, Any]:
        for attempt in range(1, self.attempts + 1):
            try:
                return await asyncio.to_thread(self._post, body)
            except RetryableProviderError as exc:
                if attempt == self.attempts:
                    raise
                # Full jitter. A fleet of runners retrying on one schedule is
                # what turns a single rate-limit response into a thundering herd.
                ceiling = min(8.0, 0.5 * 2 ** (attempt - 1))
                logger.warning(
                    "provider.retry", attempt=attempt, of=self.attempts, reason=exc.message
                )
                # nosec B311 - retry jitter. An attacker who can predict when this
                # process retries a rate-limited request gains nothing; a
                # cryptographic source here would be cost with no benefit.
                await asyncio.sleep(random.uniform(0, ceiling))  # noqa: S311  # nosec B311
        # Unreachable: the loop either returns or re-raises on the last attempt.
        raise ProviderError("the retry loop ended without a result.")  # pragma: no cover

    def _post(self, body: bytes) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **self.extra_headers,
        }
        # The URL scheme was checked against an allowlist in the constructor and
        # cannot be file:// or another local scheme.
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            # nosec B310 - the scheme was checked against ALLOWED_SCHEMES in the
            # constructor, so this cannot be file:// or another local scheme. The
            # host comes from configuration an operator wrote, never from a
            # suite file's case data.
            opened = urllib.request.urlopen(request, timeout=self.timeout_s)  # noqa: S310  # nosec B310
            with opened as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = _read_error(exc)
            message = f"the provider returned HTTP {exc.code}: {detail}"
            remedy = "Check the model name, the credential and the endpoint."
            if exc.code in RETRYABLE_STATUS:
                raise RetryableProviderError(message, remedy=remedy) from exc
            raise ProviderError(message, remedy=remedy) from exc
        except TimeoutError as exc:
            raise RetryableProviderError(
                f"the provider did not answer within {self.timeout_s}s."
            ) from exc
        except urllib.error.URLError as exc:
            # URLError is the parent of HTTPError, so this clause must stay
            # below it; a socket timeout also surfaces here on some platforms.
            raise RetryableProviderError(
                f"the provider could not be reached: {exc.reason}"
            ) from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"the response exceeded {MAX_RESPONSE_BYTES} bytes.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(f"the provider's response is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderError("the provider's response is not a JSON object.")
        return payload

    def _to_completion(
        self, payload: dict[str, Any], request: CompletionRequest, elapsed_ms: float
    ) -> Completion:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("the provider's response contains no choices.")
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError("the provider's first choice is not an object.")
        message = first.get("message")
        text = ""
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
        raw_usage = payload.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return Completion(
            text=text,
            model=str(payload.get("model", request.model)),
            provider=self.name,
            latency_ms=elapsed_ms,
            prompt_tokens=_as_int(usage.get("prompt_tokens")),
            completion_tokens=_as_int(usage.get("completion_tokens")),
            finish_reason=str(first.get("finish_reason", "stop")),
        )


def _read_error(exc: urllib.error.HTTPError) -> str:
    """Return the body of an error response, clipped.

    Clipped because a gateway's HTML error page is not a useful log line, and
    because an error body can carry back whatever was sent — including, on a
    misconfigured proxy, the request headers.
    """
    try:
        raw = exc.read(2048).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - the error path must not raise
        return exc.reason if isinstance(exc.reason, str) else "no detail"
    return " ".join(raw.split())[:400] or "no detail"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)

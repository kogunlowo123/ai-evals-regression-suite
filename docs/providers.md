# Providers, cassettes and hermetic mode

Four providers. Three cannot open a socket; one can, and that one is the only
network-facing code in the package.

`aievals list providers` prints the registry.

## Hermetic mode

**Default on.** `build_provider` refuses to *construct* a provider that can
reach the network when a run is hermetic, and it refuses before the object
exists.

```console
$ aievals gate --suite suites/live.yaml --baseline baselines/live.json
provider 'openai_compatible' can reach the network and this run is hermetic.
  Record cassettes once with 'aievals record', commit them, and run the gate
  against 'replay'. Pass --allow-network to opt out, which makes the run's
  verdict depend on a third party.
```

A guard that fired when the socket opened would already have allowed the object
to be built, and whether it then fired would depend on which code path a run
happened to take. Refusing construction makes the guarantee independent of
control flow. See ADR-005.

`Provider.reaches_network` defaults to `True`, so a new provider that forgets to
declare it is refused rather than admitted.

## `replay`

Serves recorded completions. This is what a gate runs against.

```yaml
provider:
  name: replay
  model: gpt-4o-mini      # recorded for the report; part of the cassette key
```

```bash
aievals gate --suite s.yaml --cassette cassettes/s.json --baseline b.json
```

`--cassette` overrides whatever provider the suite names, which is the normal CI
shape: the suite records the model it was recorded against, and the job replays.

**A miss is an error, never a live call.** A replay that quietly reached the
network on a miss would be a hermetic run that is not hermetic. The error names
what invalidates a recording.

## `scripted`

Answers from a table or a callable supplied in code. For tests, and for
self-contained examples.

```yaml
provider:
  name: scripted
  options:
    responses:
      "How long do I have to request a refund?": "Within 30 days."
```

## `echo`

Returns the prompt. Not a model — it exists so `aievals doctor` can prove the
harness runs a case end to end on a machine with no recordings and no
credentials.

## `openai_compatible`

Speaks the `/v1/chat/completions` shape, which almost every gateway, local
runtime and hosted vendor exposes.

```yaml
provider:
  name: openai_compatible
  model: gpt-4o-mini
  temperature: 0.0
  max_tokens: 512
  options:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY     # the NAME of a variable
    timeout_s: 60
    attempts: 3
    headers: {X-Tenant: acme}
```

**A credential never appears in a suite file.** `api_key` is refused with an
error naming the alternative. Suite files are committed and shared; there is no
code path here that accepts a key as a literal.

**Retries.** `408`, `409`, `429`, `500`, `502`, `503`, `504` and connection
failures are retried with exponential backoff and **full jitter**. A `401` is
not — retrying it three times wastes time and tells nobody anything.

**It is exercised in CI.** Request construction, authentication headers,
timeouts, retry and backoff, error mapping and usage parsing all run under test
against a fake HTTP server on `127.0.0.1`. What CI never does is talk to a
vendor. A provider that is never exercised is untested code in the one
security-sensitive spot in the package.

**Token counts are `None` when a provider does not report them**, never zero.
Zero would be a measurement, and a token budget silently satisfied by a provider
that counts nothing is a budget that is not enforced — the gate fails such a run
with `TOKEN_BUDGET_UNMEASURABLE`.

`urllib.request` rather than a client library: the dependency would exist to
save twenty lines in a component most users never construct.

## Cassettes

A cassette maps a request fingerprint to the completion a model gave.

```bash
aievals record --suite suites/support.yaml --out cassettes/support.json \
  --allow-network
```

**The key is a content address of the request:** model, prompt, system message,
temperature and token limit. Change any of them and the recording no longer
applies — which is correct, and is why a miss is an error rather than a fallback.

**Redaction runs over the assembled cassette immediately before it is written.**
Cassettes are committed to repositories; a completion quoting a key would
otherwise be a credential in version control. The fingerprint is computed from
the *original* request, so redacting the stored copy does not break lookup. Each
file records what was redacted:

```json
"redaction": {"applied": true, "count": 1, "rules": ["openai_api_key"]}
```

**Commit them.** They are the reason a gate can run in thirty seconds with no
credential, and reviewing a cassette diff is a good way to see what actually
changed when a model was swapped.

## Adding a provider

1. Subclass `Provider` in `providers/`.
2. **Declare `reaches_network`.** This is the whole of hermetic enforcement.
3. Decorate with `@provider("your_name")`.
4. Import it in `providers/__init__.py` for the registration side effect —
   after the offline providers, so a hermetic run never depends on a
   network-facing module importing cleanly.
5. If it opens a socket, exercise it against a loopback server in
   `tests/integration/test_providers.py`. Not against a vendor.

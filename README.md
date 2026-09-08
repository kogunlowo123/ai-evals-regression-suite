# aievals — evaluations that fail builds

[![CI](https://github.com/kogunlowo123/ai-evals-regression-suite/actions/workflows/ci.yml/badge.svg)](https://github.com/kogunlowo123/ai-evals-regression-suite/actions/workflows/ci.yml)
[![Security](https://github.com/kogunlowo123/ai-evals-regression-suite/actions/workflows/security.yml/badge.svg)](https://github.com/kogunlowo123/ai-evals-regression-suite/actions/workflows/security.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An LLM evaluation harness built on one observation: **an evaluation that prints
a score is a dashboard, and a dashboard has never blocked a merge.**

Most eval tooling stops at a number in a table. A regression then arrives as a
slightly different number, in a report nobody is required to read, and ships.
This project treats evaluations as tests. They have a baseline, they exit
non-zero, and — the part almost nothing else does — **the suite itself is tested
for whether it would notice a regression at all.**

```console
$ aievals gate --suite suites/support.yaml --cassette cassettes/support.json \
    --baseline baselines/support.json --mutate
FAILED  support: 11/12 (91.7%) via replay
  FAIL  REGRESSION: 1 case(s) passed at baseline and fail now.
        refund-window
  warn  FLAKY_CASES: 0 case(s) disagreed with themselves across samples.
$ echo $?
2
```

---

## The three claims, and the code that enforces each

### 1. A suite that cannot fail is not a suite

`aievals mutate` takes responses the suite **accepted**, corrupts them, and
re-grades. Empty the response. Replace it with a refusal. Cut it to 30%. Strip
every digit. Break the JSON. If the verdict does not flip, the suite has a hole
— and the report names the case, the corruption, and what should have been
noticed:

```console
$ aievals mutate --suite suites/support.yaml --cassette cassettes/support.json
  SURVIVED  refund-window / drop_numbers: a suite whose case turns on a figure
            must check the figure
            the suite accepted: "You can request a refund within  days of purchase."
FAIL  the mutation score 92.3% is below the required 100.0%.
```

That suite scored 12/12 on the model. It would also have scored 12/12 on a model
that had forgotten the number.

This is fault injection into the *graded artefact*, not mutation testing in the
`mutmut` sense — there is no code under test, and what is being measured is the
suite's power to discriminate. The meta-gate needs no extra model calls:
grading is a pure function of the response, so it runs over the responses the
gate already produced.

### 2. A score is not a verdict

17/20 against 16/20 is a coin flip. A 10-point drop is not. The gate splits by
run mode rather than piling up conditions:

| Run mode | What a flipped case means | Rule |
| --- | --- | --- |
| Replay, one sample per case | Deterministic — the change caused it | **Any** pass→fail flip fails the build. No statistics. |
| Sampled (`--samples n`) | Could be noise | Paired **exact McNemar test** over the discordant pairs decides. |

The paired test matters. Two runs of one suite are the same cases measured
twice, not two independent samples; the two-proportion z-test most harnesses
reach for is the wrong test and throws away the pairing. The exact binomial form
is valid at three discordant pairs, where the chi-squared approximation needs
about twenty-five — and real suites have twenty to two hundred cases with a
handful of flips.

A subtlety worth stating because getting it wrong builds dead code: under a
single deterministic sample, "the pass rate dropped significantly" *implies*
"some case flipped". Writing both as independent gate conditions gives you a
statistics module that can never be the sole reason a build is red.

### 3. An evaluation you cannot re-run is an anecdote

- **Suites are content-addressed.** The digest covers what is measured — cases,
  prompts, graders, thresholds — and deliberately not prose, tags or file
  ordering, so editing a comment does not invalidate a baseline.
- **A baseline recorded against a different suite is a hard failure**, not a
  warning. A comparison against the wrong thing looks exactly like a real result.
- **Hermetic by default.** `aievals gate` refuses to *construct* a provider that
  can open a socket. Not "refuses to send" — refuses to build the object, so the
  guarantee does not depend on which code path a run takes.

---

## Install

```bash
uv sync --extra dev          # or: pip install -e ".[dev]"
uv run aievals doctor
```

Python 3.12. Four runtime dependencies: pydantic, pydantic-settings, structlog,
PyYAML. The HTTP client is `urllib` from the standard library.

## A suite

```yaml
name: support
description: What the support assistant must get right.
provider:
  name: openai_compatible
  model: gpt-4o-mini
  temperature: 0.0
  options:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY      # the *name* of a variable, never a key

thresholds:
  min_pass_rate: 0.95
  max_p95_latency_ms: 4000

default_graders:
  - type: contains_none
    params:
      values: ["As an AI language model", "I cannot help"]

cases:
  - id: refund-window
    prompt: How long do I have to request a refund?
    system: You are a support agent for Acme. Be brief and specific.
    rationale: Policy page section 4.2. Wrong number here becomes a chargeback.
    tags: [policy]
    graders:
      - type: numeric_close
        params: { expected: 30, where: first }
      - type: contains_all
        params: { values: ["days"] }
```

**A suite file cannot execute code.** Graders are named against a registry;
there is no import path, no dotted callable, no expression. Parsing is
`yaml.safe_load`, never `yaml.load`. Suite files arrive in pull requests, and
the convenient design — `grader: mypkg.checks:looks_right` plus `importlib` — is
arbitrary code execution on a runner holding repository credentials.

## The workflow

```bash
# once, deliberately, with egress
aievals record --suite suites/support.yaml --out cassettes/support.json --allow-network

# once, to fix the reference point
aievals baseline --suite suites/support.yaml --cassette cassettes/support.json \
  --out baselines/support.json

# every build, hermetic, exit 2 on a regression
aievals gate --suite suites/support.yaml --cassette cassettes/support.json \
  --baseline baselines/support.json --mutate \
  --junit-out reports/evals.xml --markdown-out reports/evals.md
```

Exit codes: `0` passed, `2` a gate failed, `3` the harness could not run. CI
needs to tell a regression from an outage, and one non-zero code cannot.

## Graders

Eight deterministic graders and one model-graded, each with a negative control
in the test suite — a case it must *reject*.

| Grader | Checks |
| --- | --- |
| `contains_all` | Every value present; score is the fraction found |
| `contains_none` | House rules: no boilerplate, no leaked system prompt |
| `exact` | Response equals a string |
| `regex` | A pattern matches, or must not — with a static ReDoS guard |
| `ordering` | Values appear in sequence |
| `json_schema` | Parses as JSON and fits a subset schema |
| `numeric_close` | A figure within tolerance, absolute or relative |
| `set_overlap` | Recall against a reference set, for partial credit |
| `judge` | A second model, replayed from a cassette |

Two of these have a design decision worth reading `docs/graders.md` for.

**`regex` refuses catastrophic patterns before compiling them.** A suite file is
untrusted input; `(a+)+b` against forty `a`s is one `re.search` that never
returns, and no timeout above it helps — `asyncio.wait_for` around
`asyncio.to_thread` abandons the coroutine but cannot stop the thread, and
Python has no way to interrupt a running match.

**`judge` is a provider, so it replays.** LLM-as-judge is normally the part CI
cannot run — which means it is never exercised, which means nobody knows whether
it works. Recording the judge makes a model-graded case as deterministic as an
exact match, and an unparseable judge answer fails the case rather than being
skipped: a judge that cannot be understood has not endorsed anything.

## What this does not do

- **It does not compute semantic similarity.** No embeddings, no vector
  dependency. Every grader here is deterministic or replayed, which is what lets
  a flipped case be treated as a regression.
- **It does not host a dashboard.** It writes JSON, JUnit XML and Markdown, and
  the CI you already have displays them.
- **It does not pick your cases for you.** The hard part of evaluation is
  knowing what to measure; this tool makes sure the measurement holds.
- **A high mutation score does not mean the suite is right.** It means the suite
  would notice a corrupted answer. A suite of twenty cases about the wrong
  requirement scores perfectly and measures nothing that matters.

## Documentation

| | |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The design, and eight decision records |
| [THREAT-MODEL.md](THREAT-MODEL.md) | What is defended, and what is not |
| [docs/suites.md](docs/suites.md) | The suite file schema |
| [docs/graders.md](docs/graders.md) | Every grader and its parameters |
| [docs/gating.md](docs/gating.md) | The gate rules and the statistics |
| [docs/mutation.md](docs/mutation.md) | The meta-gate |
| [docs/providers.md](docs/providers.md) | Cassettes, hermetic mode, the HTTP provider |
| [docs/ci.md](docs/ci.md) | Wiring it into a pipeline |
| [SECURITY.md](SECURITY.md) | Reporting, and the residual risks |

## License

MIT. See [LICENSE](LICENSE).

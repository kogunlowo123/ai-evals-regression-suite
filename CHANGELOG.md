# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-09-08

First release. An LLM evaluation harness whose evaluations are tests: they have
a baseline, they exit non-zero, and the suite itself is tested for whether it
would notice a regression at all.

### Added

**The gate**

- `aievals gate` — run a suite, compare it against a baseline, apply every rule,
  exit `2` on a failure and `3` when the harness itself could not run.
- Rules: `REGRESSION`, `SIGNIFICANT_REGRESSION`, `STALE_BASELINE`,
  `PASS_RATE_BELOW_MINIMUM`, `LATENCY_BUDGET_EXCEEDED`, `TOKEN_BUDGET_EXCEEDED`,
  `TOKEN_BUDGET_UNMEASURABLE`, `CASE_ERRORS`, plus `FLAKY_CASES` and
  `CASES_REMOVED` as warnings.
- The rules are selected by **run mode** rather than OR'd together (ADR-002).
  Under replay any flip is fatal; under sampling the paired test decides. Both
  conditions at once would have produced a statistics module that could never be
  the sole reason a build is red.

**The meta-gate**

- `aievals mutate` — corrupt responses the suite accepted and assert the verdict
  flips. Eleven mutators in a core set and an extended diagnostic set, each
  declaring what it can act on, all deterministic under a seed.
- Judge graders are excluded and the exclusion is reported; a corruption that
  changes nothing is discarded rather than scored; a run with no applicable
  mutants scores 0.0, not 1.0.
- No extra model calls: grading is a pure function of the response.

**Statistics**

- McNemar's test in its exact binomial form, over the discordant pairs only —
  paired because two runs of one suite are the same cases measured twice, and
  exact because a suite has twenty to two hundred cases and a handful of flips.
- Wilson score intervals, reported and never gated on, because the textbook
  approximation claims a zero-width interval at 20/20.

**Suites**

- YAML or JSON, `yaml.safe_load` only, size-capped before parsing, case-capped
  after. Graders named against a registry — no import paths, no callables, no
  expressions (ADR-004).
- Content-addressed. The digest covers what is measured and deliberately not
  prose, tags or file ordering; a baseline recorded against a different suite is
  a hard failure (ADR-008).

**Graders**

- Eight deterministic — `contains_all`, `contains_none`, `exact`, `regex`,
  `ordering`, `json_schema`, `numeric_close`, `set_overlap` — each with a
  negative control in the tests.
- One model-graded — `judge` — which **runs in CI**, because the judge is a
  provider and replays from a cassette (ADR-006). An unparseable judge answer
  fails the case rather than being skipped.
- A subset JSON Schema validator that **refuses unimplemented keywords when the
  schema is registered** rather than ignoring them when a document is checked.
- A static ReDoS guard applied before compilation (ADR-010).

**Providers**

- `replay`, `scripted`, `echo`, and one `openai_compatible` HTTP provider.
- Hermetic by default, enforced at **construction** (ADR-005): a provider that
  can open a socket cannot be built in a hermetic run.
- Cassettes keyed by a content address of the request, redacted before they are
  written, because they are committed to repositories.
- The HTTP provider refuses an `api_key` literal and names the
  environment-variable alternative. It is exercised in CI against a loopback
  server — never against a vendor.

**Reports**

- JSON on stdout, JUnit XML so a red evaluation looks like a red test, and
  Markdown for a job summary.
- Responses omitted by default; the full response is replaced by a length and a
  digest, and the precise residual — a bounded quoted fragment in a grader
  detail — is stated in `SECURITY.md` rather than glossed over.
- Redaction over the **assembled** artefact, once, last (ADR-009).

**Project**

- 560 tests across five layers at 94% coverage against a 90% gate.
- A `meta` layer that breaks one requirement at a time and asserts the build
  goes red — including deleting a case's graders and asserting the meta-gate
  catches it. Without it, "CI fails on real regressions" is an unsupported claim.
- CI: lint, `ruff format --check`, `mypy --strict`, a five-layer test matrix, a
  coverage gate, the tool gating its own worked example, a check that the
  example cassettes still match the suite, three executable examples, and a
  distribution build that installs the wheel and runs its console script.
- Security workflow: gitleaks over the tree **and the full history**, bandit,
  `pip-audit` against the locked set, CodeQL `security-extended`, and Trivy over
  the filesystem and the image. No `continue-on-error` anywhere.
- Container workflow: build, image scan, a non-root assertion, and a smoke test
  that runs the project's own gate *inside* the image and asserts the gate can
  still fail there.
- A documentation site generated from the repository's own Markdown, failing the
  build on a broken internal link.

### Notes on decisions

Recorded in [ARCHITECTURE.md](ARCHITECTURE.md) because they are the ones a
reader is most likely to question:

- **The gate splits by run mode** (ADR-002), because writing both the per-case
  and the aggregate rule as independent conditions makes the second unreachable.
- **A paired exact test, not a two-proportion z-test** (ADR-003). The cases are
  paired; the usual test assumes independence that is not there and is invalid
  at the sample sizes evaluation suites actually have.
- **Fault injection into the response is the core deliverable** (ADR-007), and
  it is *not* mutation testing in the `mutmut` sense — the docstring and the
  documentation both say what is actually mutated.

### Defects found and fixed during development

Recorded because the tests that caught them are the reason to trust the rest:

- **The meta-gate found a hole in this repository's own example suite.** The
  `refund-window` case checked the figure and the opening of the answer, so
  `truncate` survived: a response cut to 30% still passed. The fix was to add
  an expectation drawn from the end of the answer, and it is commented as such
  in `examples/support.yaml`.
- **A corruption that changed nothing was scored as a survivor.** Stripping
  formatting from unformatted prose returns it unchanged, and the suite was
  being blamed for the mutator's own no-op. Identical mutants are now discarded
  rather than counted either way.
- **The command line crashed on a Windows console.** stdout defaults to cp1252,
  the report is JSON, and model output is arbitrary Unicode: a single `≥` in a
  grader detail raised `UnicodeEncodeError` *after* the whole run had been paid
  for. Found by the end-to-end layer, which is the only one that runs a real
  process with a real console encoding.
- **"Responses are omitted by default" was too strong a claim.** A grader detail
  quotes the response it rejected, so personal data could reach a report the
  documentation said was clean. The behaviour is now bounded and the claim is
  stated precisely, in three places, with tests pinning both halves.
- **An `assert` was guarding a runtime invariant** in the scripted provider.
  `assert` is removed under `-O`, so the guard would be absent in exactly the
  deployment where a stray `None` is hardest to diagnose.

[Unreleased]: https://github.com/kogunlowo123/ai-evals-regression-suite/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kogunlowo123/ai-evals-regression-suite/releases/tag/v0.1.0

# Contributing

Thanks for taking the time to contribute. This document describes the local
workflow, the quality bar enforced in CI, and how changes are reviewed.

## Prerequisites

- Python 3.12 (the project pins `>=3.12,<3.13`)
- [uv](https://docs.astral.sh/uv/) 0.10 or newer
- Docker (optional, only needed for container work)

`make` is convenient on Linux and macOS but is not required. `python tasks.py`
is the cross-platform entry point and the source of truth; the `Makefile`
simply delegates to it.

## Getting set up

```bash
python tasks.py setup          # uv sync --locked --group dev --group docs
cp .env.example .env
python tasks.py doctor         # proves the install works end to end
```

`.env` is only needed for `aievals record`, which is the one command that
reaches a network. Everything else — the whole test suite, the gate, the
meta-gate, every example — runs offline with no credential.

Never commit a populated `.env`. `.gitignore` excludes it and CI runs a secret
scan over both the working tree and the full git history.

## Development loop

| Task | Command |
| --- | --- |
| Format | `python tasks.py fmt` |
| Lint | `python tasks.py lint` |
| Type check | `python tasks.py typecheck` |
| Unit tests | `python tasks.py test-unit` |
| Integration tests | `python tasks.py test-integration` |
| Security tests | `python tasks.py test-security` |
| End-to-end tests | `python tasks.py test-e2e` |
| The gate's own negative controls | `python tasks.py test-meta` |
| Full suite + coverage gate | `python tasks.py test` |
| Gate the worked example, as CI does | `python tasks.py gate` |
| The meta-gate | `python tasks.py mutate` |
| What is this suite blind to? | `python tasks.py mutate-extended` |
| Rebuild the example fixtures | `python tasks.py fixtures` |
| Run every example | `python tasks.py examples` |
| Documentation site | `python tasks.py site` |
| Local security scans | `python tasks.py security` |
| Container image + smoke test | `python tasks.py smoke` |

Run `python tasks.py --list` for the full list.

## Quality bar

A change is mergeable when all of the following hold:

1. `ruff check` and `ruff format --check` are clean.
2. `mypy --strict` reports no errors.
3. The full test suite passes and line coverage is at least **90%**.
4. `aievals gate --mutate --min-caught 1.0` passes over the worked example,
   in-tree **and** inside the built container image.
5. `bandit` and `pip-audit` report no unresolved findings. If a dependency
   vulnerability has no upstream fix, add a justified, dated entry to
   `security/audit-exceptions.md` and the identifier to
   `security/audit-ignores.txt`.
6. No secret scanner finding, in the tree or in history.
7. Every example still runs. They are documentation that executes; one that
   stops working is a README that lies.
8. The documentation site builds. The builder fails on a broken internal link,
   so a renamed file fails the pull request rather than the deployment.

## Tests

Five layers, each runnable on its own:

- `unit` — pure logic, no I/O and no network.
- `integration` — components wired together against real temporary files, and
  the HTTP provider against a loopback server.
- `security` — adversarial cases. **A failure here is a security regression**,
  not a bug.
- `e2e` — the command line as a real process, so exit codes and the
  stdout/stderr split are actually asserted.
- `meta` — the gate's negative controls. See below.

New behaviour needs a test at the lowest layer that can express it. Tests that
assert nothing meaningful (`assert True`, calls with no assertions) are rejected
in review.

### Four rules specific to this repository

**Every grader needs a negative control.** A test that only shows a grader
passing is satisfied by a grader that returns `True` unconditionally. The case
it must *reject* is the assertion that matters.

**Every gate rule needs a case that turns the build red.** `tests/meta/` breaks
one requirement at a time and asserts the corresponding finding fires, with a
control asserting the healthy repository is green. If that file is deleted, the
claim on the front of the README stops being supported by anything.

**A security control is asserted against the whole serialised artefact**, not
against one member of it. The interesting failure is a control that exists and
is not wired up — which has happened repeatedly in this series, always with
redaction, and always because a derived string was attached after the pass.

**Fixtures that look like credentials are built by concatenation** — `"AKIA" +
"IOSFODNN7EXAMPLE"` — so a scanner does not report a fixture as a finding and a
reader can see at a glance that nothing was ever valid. Add the file to
`.gitleaks.toml` with a written reason.

## Commits and pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:`.
- Keep the subject line under 72 characters and use the imperative mood.
- One logical change per pull request.
- Describe the behaviour change, the risk, and how you verified it.
- Update `CHANGELOG.md` under `## [Unreleased]` for anything user-visible.

## Changing the worked example

`examples/support.yaml` is a real suite with real recordings, and CI checks that
they still match. After editing it:

```bash
python tasks.py fixtures    # rebuild the cassettes and the baseline
python tasks.py gate        # confirm it is still green
```

Commit the regenerated files in the same pull request. A reviewer looking at the
diff should see both the new expectation and the verdicts it produced.

## Adding a grader, a provider or a mutator

Each has a registry, and each registry is a boundary rather than a convenience:

- **A grader** (`docs/graders.md`) — validate parameters in `__init__`, so a
  misconfigured suite fails at load time naming the case. Ship a negative
  control.
- **A provider** (`docs/providers.md`) — declare `reaches_network`. That flag is
  the whole of hermetic enforcement. If it opens a socket, exercise it against
  a loopback server, never against a vendor.
- **A mutator** (`docs/mutation.md`) — declare what it is `applicable` to, and
  make it deterministic under its seed. A mutator that fires on content it
  cannot change produces survivors that blame the suite for its own no-op.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow the process in
[SECURITY.md](SECURITY.md).

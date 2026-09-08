# Security policy

## Reporting a vulnerability

Report privately through GitHub's advisory workflow:
<https://github.com/kogunlowo123/ai-evals-regression-suite/security/advisories/new>

Please do not open a public issue for a vulnerability.

Include what you have: the suite file, cassette or command that triggers it,
what you expected, what happened, and the version or commit. A proof of concept
helps but is not required.

**What to expect.** This is a portfolio project maintained by one person, not a
funded product, and it is fairer to say so than to publish a service-level
agreement nobody is on call for. Acknowledgement within a week is realistic. If
a report is valid it will be fixed and credited, and the fix will say what was
wrong rather than describing it as "hardening".

## Supported versions

The `main` branch. There is no backport policy for older tags.

## What this protects, and what it does not

[THREAT-MODEL.md](THREAT-MODEL.md) is the document to read before running this
on anything that matters. The short version:

**In scope.** Code execution through a suite file; resource exhaustion through
one; credentials reaching a published artefact; credentials committed in a
cassette or a suite; personal data in a report; unintended network egress from a
gate run; prompt injection through a recorded response; a gate that silently
stops being able to fail.

**Out of scope.** An attacker who can already modify the Python in `graders/` —
they do not need a suite file. Vulnerabilities in the vendor you point the HTTP
provider at. Transport security, which is your network's problem.

## The thing most worth understanding

**A suite file is untrusted input that arrives in a pull request and runs on a
machine holding repository credentials.**

That is the whole security story. Everything else follows:

- Graders are resolved from a **registry by name**. Nothing is imported from
  suite data — no dotted paths, no callables, no expressions. `importlib`,
  `__import__` and `eval` do not appear in `graders/registry.py`, and a test
  reads the module's own source to assert it.
- Parsing is `yaml.safe_load`. `!!python/object/apply:os.system` is a validation
  error, not a shell.
- Regular expressions from a suite file are checked for catastrophic
  backtracking **before compilation**, because no timeout can stop a running
  `re` match.
- Files are size-capped *before* parsing, and suites are case-capped.

## Known residual risks

These are stated because a security document claiming complete coverage is one
nobody should believe.

1. **Redaction recognises shapes, not entropy.** A high-entropy password
   assigned to a variable called `x` is indistinguishable from a hash.
2. **A report may quote a bounded fragment of a response.** With
   `--include-responses` off the full response is replaced by a length and a
   digest, but a grader detail can still carry up to 80 characters, because that
   fragment is the reason the build is red. A run whose prompts carry data that
   must not appear at all belongs behind a private artefact store.
3. **The ReDoS guard is heuristic.** Recognising every exponential regular
   expression is undecidable. A merely-slow pattern is bounded only by the
   per-case timeout, and a single pathological match inside one case cannot be
   interrupted — Python offers no way to stop a running `re` match.
4. **The meta-gate measures discrimination, not correctness.** A suite that
   catches every corruption may still be asking the wrong questions.
5. **`--allow-stale-baseline` exists.** It downgrades a digest mismatch to a
   warning. The warning says the comparison is not a fact about the change, but
   somebody still has to read it.
6. **Prompt-injection marking depends on the judge model.** The response under
   judgement is fenced and the system message says it is untrusted data.
   Nothing here can make a model honour that.

## Network egress

**A gate run makes no outbound request.** Hermetic mode is the default and is
enforced at construction: a provider that can open a socket cannot be built
(ADR-005).

The package does contain one HTTP client — `providers/openai_compat.py` — used
when a suite selects it *and* the run passes `--allow-network`, which `aievals
record` requires. Its URL scheme is checked against an allowlist of `http` and
`https`, so a `file://` base URL cannot turn it into a file reader. It is
exercised in CI against a fake server on `127.0.0.1`, never against a vendor.

## Credentials

**A credential cannot be written in a suite file.** The HTTP provider refuses an
`api_key` option and names the environment-variable alternative in the error.
There is no code path that accepts one as a literal.

**A credential cannot be committed in a cassette.** Redaction runs over the
assembled cassette immediately before it is written, and each file records the
rules that fired.

**There are none in this repository, and there never were.** The
credential-shaped strings in the tests are constructed by concatenation —
`"AKIA" + "IOSFODNN7EXAMPLE"` — so a scanner does not report a fixture as a
finding and a human can see at a glance that nothing here was ever valid. CI
enforces it: gitleaks runs over the working tree **and the full git history** on
every push, and the build fails on a finding.

## Verifying the claims yourself

```bash
uv run pytest -m security          # adversarial cases
uv run pytest -m meta              # the gate proving it can fail
uv run bandit -c pyproject.toml -r src
uv run python tasks.py security    # bandit + pip-audit against the locked set

python examples/mutation_demo.py   # two suites at 100%, one measuring nothing
python examples/statistics_demo.py # why the paired exact test
```

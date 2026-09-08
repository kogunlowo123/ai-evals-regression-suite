# Threat model

## What this thing is, in security terms

`aievals` is a command-line tool that reads a data file written by someone else,
runs it, and writes artefacts that get uploaded and shared. That sentence
contains every interesting threat:

- **the data file is untrusted** — it arrives in a pull request;
- **it runs on a CI runner** — which holds repository credentials, and often a
  package-registry token;
- **the artefacts are published** — a JUnit report in a pull request, a Markdown
  summary in a job summary, a cassette committed to the repository;
- **the content it handles is model output** — arbitrary text, derived from
  prompts that may carry customer data.

It is not a service. There is no listener, no authentication, and no
multi-tenancy, so an entire class of threat does not apply — and pretending
otherwise would pad this document at the cost of the parts that matter.

## Trust boundaries

```
   pull request ──▶ suite file ──▶ [ loader ] ──▶ suite model
                                       ▲
                              THE boundary: after this,
                              suite content is data with
                              a known shape and nothing else

   vendor ──▶ completion ──▶ [ grader ] ──▶ verdict ──▶ [ redaction ] ──▶ artefact
                                                             ▲
                                                    the last thing that
                                                    touches anything
                                                    leaving the process
```

## Threats

### T1 — Arbitrary code execution through a suite file

**The attack.** A pull request adds a suite whose grader is an import path, or
whose YAML carries `!!python/object/apply:os.system`. CI runs it. The attacker
has code execution as the runner, with whatever tokens it holds.

**Mitigation.** Graders are resolved from a registry by name; nothing is
imported from suite data, and `graders/registry.py` contains no `importlib`, no
`__import__` and no `eval` — asserted by a test that reads its own source.
Parsing is `yaml.safe_load`. Grader names must match `[a-z][a-z0-9_]*`, so an
import path is not even a well-formed name.

**Residual.** A malicious *pull request* can still change the Python in
`graders/`. That is what review is for, and it is a different control.

### T2 — Resource exhaustion through a suite file

**The attack.** A 40-byte YAML alias bomb; a ten-million-case suite; a
`regex` grader containing `(a+)+b`.

**Mitigation.** A size cap applied *before* parsing (4 MiB). A case cap
(`MAX_CASES = 10 000`). A static ReDoS guard applied before compilation
(ADR-010), a pattern-length cap, and an allowlist of regex flags that excludes
`LOCALE` — which would also make a suite non-reproducible. Per-case timeouts and
a bounded concurrency limit.

**Residual.** A regular expression outside the guard's known shapes that is
merely *slow* is bounded only by the per-case timeout. Recognising every
exponential pattern is undecidable.

### T3 — Credentials in an artefact

**The attack.** A model quotes an API key it was shown, or a fixture contains
one. It lands in a JUnit report attached to a public pull request, or in a
cassette committed to the repository.

**Mitigation.** Fourteen credential shapes, redacted over the **assembled**
structure immediately before it is written (ADR-009). Applied to the JSON
report, the JUnit document, the Markdown summary and every cassette. The rules
that fired are named in the output, so a reviewer can see what kind of mistake
was made. `tests/security/test_no_leaks.py` asserts against whole serialised
artefacts across three credential shapes and six output paths.

**Residual.** Redaction recognises *shapes*, not entropy. A high-entropy
password assigned to a variable called `x` is indistinguishable from a hash.

### T4 — A credential in a suite file

**The attack.** Someone writes `api_key: sk-live-...` in a suite, and commits
it. Documentation saying not to does not prevent it.

**Mitigation.** The HTTP provider **refuses** an `api_key` option with an error
naming the alternative. There is no code path that accepts a credential as a
literal; the suite names an *environment variable*, and the provider reads it.

**Residual.** A suite could still name an environment variable that a CI job
populates with the wrong secret. That is a deployment mistake this tool cannot
see.

### T5 — Personal data in a published report

**The attack.** Evaluation prompts carry support tickets, medical questions,
customer names. Responses derived from them end up in a report artefact that
lives for months.

**Mitigation.** Responses are **omitted by default**: replaced by a length and a
digest, so two runs are still distinguishable. `--include-responses` is a
deliberate opt-in.

**Residual, stated precisely.** A grader detail may still quote up to 80
characters of the response, because that fragment is the reason the build is
red. A run whose prompts carry data that must not appear at all belongs behind a
private artefact store. `--include-responses` reduces exposure; it does not
eliminate it, and the flag's help text says so.

### T6 — A gate that cannot fail

**The attack.** Not an attacker — entropy. A suite is weakened over time: a
grader deleted to make a flaky case green, a threshold lowered, a case removed.
Every signal stays green and the evaluation stops measuring anything.

**Mitigation.** This is what `aievals mutate` exists for (ADR-007), and it is
the threat this repository treats as its subject. Removing a case is reported as
a finding even though it *raises* the pass rate. `tests/meta/` breaks one
requirement at a time and asserts the build goes red — including deleting a
case's graders and asserting the meta-gate catches it.

**Residual.** A high mutation score means the suite would notice a corrupted
answer. It does not mean the suite is asking the right questions. Twenty cases
about the wrong requirement score perfectly.

### T7 — A comparison against the wrong thing

**The attack.** A baseline recorded from a different suite — an older revision,
a colleague's branch, a copy with two cases removed. The comparison looks
entirely normal and its verdict is meaningless.

**Mitigation.** Suites are content-addressed and a digest mismatch is a hard
failure that suppresses every finding downstream of it (ADR-008). The digest
deliberately excludes prose, so it does not cry wolf.

**Residual.** `--allow-stale-baseline` exists, because sometimes the alternative
is a workflow nobody can complete. It downgrades the finding to a warning and
the warning says the comparison is not a fact about the change.

### T8 — Unintended egress

**The attack.** A gate run reaches a vendor — costing money, leaking prompt
content to a third party, and making the verdict depend on someone else's
uptime.

**Mitigation.** Hermetic by default, enforced at *construction* (ADR-005). One
network-facing provider exists in the package, and it cannot be built in a
hermetic run. Its URL scheme is checked against an allowlist, so a `file://`
base URL cannot turn it into a file reader.

**Residual.** `--allow-network` exists and is required for `record`. That is the
point: recording is a deliberate, occasional act.

### T9 — Prompt injection through a fixture

**The attack.** A recorded response contains instructions addressed to whatever
reads it. The judge grader is the only component that feeds model output back to
a model.

**Mitigation.** The response under judgement is fenced, and the judge's fixed
system message states that the content is untrusted data and that instructions
inside it are part of what is being graded. The content is passed **byte for
byte** — a grader that edits what it grades is measuring its own edit.

**Residual.** Marking depends on the judge model honouring it. Nothing here can
make it. `inject_instruction` is a mutator precisely so a suite that claims to
catch this can be checked.

### T10 — Path traversal through a file argument

**The attack.** A suite, cassette or baseline path pointing somewhere it should
not.

**Mitigation.** Limited, and deliberately so. Every path comes from the
operator's own command line, not from suite data; this tool reads what the
person running it told it to read, exactly like `cat`. Suite files are checked
for a plausible suffix and a size cap, and the `origin` label used in error
messages is never opened.

**Residual.** A suite file cannot cause another file to be read — there is no
include directive, no `$ref`, and no schema resolution — which is the property
that matters.

### T11 — Supply chain

**The attack.** A compromised dependency.

**Mitigation.** Four runtime dependencies, all widely used and version-pinned by
a committed lockfile. The HTTP client is `urllib` from the standard library.
`pip-audit` runs against the locked set in CI with no `continue-on-error`;
Dependabot opens updates weekly.

**Residual.** Four is not zero. `pydantic` in particular is a large dependency,
justified by doing the validation this tool's security posture rests on.

### T12 — Denial of service against a vendor

**The attack.** Not against this tool — *by* it. A misconfigured run hammering a
rate-limited endpoint.

**Mitigation.** Bounded concurrency, a per-request timeout, a bounded retry
count, and **full jitter** on the backoff. A fleet of runners retrying on the
same schedule is what turns one 429 into an outage.

## Summary

| | Threat | Mitigated by | Residual |
| --- | --- | --- | --- |
| T1 | Code execution from a suite file | Registry, `safe_load` | Malicious PR editing Python |
| T2 | Resource exhaustion | Size/case caps, ReDoS guard, timeouts | Merely-slow patterns |
| T3 | Credentials in an artefact | Assemble-then-redact-once | Shape-based, not entropy |
| T4 | Credential in a suite file | `api_key` refused by construction | Wrong variable populated |
| T5 | Personal data in a report | Responses omitted by default | Bounded quoted fragments |
| T6 | A gate that cannot fail | The meta-gate, `tests/meta/` | Right measurement ≠ right question |
| T7 | Comparison against the wrong suite | Content addressing, hard failure | `--allow-stale-baseline` |
| T8 | Unintended egress | Hermetic at construction | `--allow-network` by choice |
| T9 | Injection through a fixture | Fenced, marked, unmodified | Depends on the judge model |
| T10 | Path traversal | No include directives at all | Operator-supplied paths are read |
| T11 | Supply chain | 4 pinned deps, `pip-audit`, CodeQL | Four is not zero |
| T12 | Hammering a vendor | Bounded concurrency, full jitter | — |

## What a production deployment must add

This is a developer tool, and a serious deployment needs things it does not
provide:

1. **A private artefact store**, if evaluation prompts carry personal data.
   `--include-responses` off is a reduction, not a boundary.
2. **Secret scanning on the repository**, because cassettes and suites are
   committed. This repository runs gitleaks over the tree *and the full
   history*; a repository using `aievals` should do the same.
3. **A policy for who may lower a threshold.** The gate is only as strong as the
   review of changes to `min_pass_rate` and to `--min-caught`.
4. **Recording in a controlled environment.** `aievals record --allow-network`
   sends every case's prompt to a vendor. That is a data-transfer decision, and
   it should be made once, deliberately, by someone who is allowed to make it.

# Architecture

## The shape of it

```
                    suite file (untrusted data)
                             │
                    ┌────────▼─────────┐
                    │ suite/           │  safe_load, validate, resolve graders
                    │  loader, models  │  by name, content-address the result
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
│ providers/     │  │ runner/         │  │ graders/        │
│ replay         │──▶ concurrency,    │──▶ registry only.  │
│ scripted, echo │  │ budgets,        │  │ no import path  │
│ openai_compat  │  │ samples         │  │ from data       │
└────────────────┘  └────────┬────────┘  └─────────────────┘
                             │ RunResult
              ┌──────────────┼──────────────┐
              │              │              │
      ┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼───────┐
      │ baseline/    │ │ mutation/  │ │ stats/      │
      │ snapshot,    │ │ meta-gate  │ │ McNemar,    │
      │ compare      │ │            │ │ Wilson      │
      └───────┬──────┘ └─────┬──────┘ └─────┬───────┘
              └──────────────┼──────────────┘
                     ┌───────▼────────┐
                     │ gate.py        │  the only module that decides
                     └───────┬────────┘
                     ┌───────▼────────┐
                     │ report/        │  JSON, JUnit XML, Markdown
                     └────────────────┘
```

One module decides. `gate.py` is the only place that turns a measurement into a
verdict; everything above it reports facts and everything below it renders them.
That separation is why the same run can be compared, mutated and published
without any of them re-running it — and why the gate's rules can be read in one
sitting.

## Where the interesting code is

| Module | What it owns | Why it is worth reading |
| --- | --- | --- |
| `gate.py` | Every rule that fails a build | The run-mode split, and the dead branch it avoids |
| `mutation/meta.py` | The meta-gate | Measures whether the suite would notice anything |
| `stats/mcnemar.py` | The paired test | Exact, because suites are small |
| `suite/digest.py` | Content addressing | Exactly what is and is not hashed, and why |
| `graders/registry.py` | Name → grader | The reason a suite file cannot execute code |
| `providers/registry.py` | Hermetic mode | One boolean, checked before construction |
| `redaction.py` | Credential removal | Walks assembled structures, never keys |

---

# Decision records

## ADR-001 — Evaluations are tests, and tests exit non-zero

**Context.** Almost every LLM evaluation tool produces a score, a table and
sometimes a web page. A regression then arrives as a slightly different number
in a report nobody is required to read.

**Decision.** The primary interface is a process that exits non-zero. `gate` is
the command; `0`, `2` and `3` are the answers; the report is a side effect.
`gate.py` is a separate module from `report/` so that no rendering decision can
change a verdict and no verdict can be softened to make a report read better.

**Consequence.** There is no dashboard, no server and no database. If a team
wants a trend line they already have one — every CI system stores JUnit XML.

**Rejected: a warning threshold.** A gate with a "warn" tier that does not fail
the build is a gate with an off switch that nobody has to justify pressing.
Warnings exist in the report (flaky cases, removed cases) but they are findings
*about* a passing build, never a way to downgrade a failing one.

## ADR-002 — The gate splits by run mode rather than accumulating conditions

**Context.** Two rules are both obviously right: fail when a case that passed
now fails, and fail when the pass rate drops by more than chance explains.

**The problem.** Under one deterministic sample per case, the second is implied
by the first. Let `b` be the pass→fail flips and `c` the fail→pass flips. The
pass rate falls exactly when `c < b`, which requires `b ≥ 1`, which means the
per-case rule has already fired. Writing both as independent conditions produces
a `stats/` module that can never be the sole reason a build is red — a whole
subsystem that is unreachable, and no test would ever show it.

**Decision.** The rules are selected by run mode, not OR'd together.

- **Replay, one sample per case.** Deterministic. Any flip fails. No statistics.
- **Sampled (`--samples n`).** A flip can be noise. The paired test decides;
  flips are still reported, as a warning, because the reader of a red build
  needs to know which cases moved.

**Consequence.** `stats/` has exactly one caller and a genuine reason to exist.
The mode is recorded on the `RunResult`, so a report says which rule applied.

## ADR-003 — A paired exact test, not a two-proportion z-test

**Context.** Under sampling, something has to decide whether a change in pass
rate is real.

**Decision.** McNemar's test in its exact binomial form, over the discordant
pairs only.

**Why paired.** Two runs of one suite are the *same cases measured twice*, not
two independent samples. The two-proportion z-test that most harnesses reach for
assumes independence that is not there, and it throws away the pairing — the
information about a regression is in *which* cases changed, not in how many
passed overall.

**Why exact.** Under the null, a discordant pair is equally likely to fall
either way, so the count is binomial with `p = 0.5`. That is computable with
integer arithmetic and valid at three discordant pairs. The chi-squared
approximation to the same test needs about twenty-five, and a real evaluation
suite has twenty to two hundred cases with a handful of flips — precisely the
region where the approximation is wrong.

**Consequence.** Five flips all in one direction gives `p = 0.0625`, which does
*not* clear a 0.05 threshold. That is the correct answer and an approximation
would have said otherwise. `examples/statistics_demo.py` prints the table.

## ADR-004 — Graders are named against a registry; a suite file cannot supply code

**Context.** A suite file has to say how a case is graded. The convenient design
is a dotted path — `grader: mypkg.checks:looks_right` — resolved with
`importlib`.

**Decision.** A suite names a grader with a short identifier, looked up in a
registry populated by decorators in this package. Nothing is imported, resolved
or evaluated from suite data. Parsing is `yaml.safe_load`, never `yaml.load`.

**Why.** Suite files arrive in pull requests and run on CI runners holding
repository credentials. The convenient design is arbitrary code execution from a
data file, and the fact that it is *convenient* is exactly why it spreads.

**Consequence.** Adding a grader is a Python change that goes through review.
Unknown grader names, and misconfigured parameters, are load-time errors that
name the case — before a single completion has been paid for.

## ADR-005 — Hermetic mode is enforced at construction, not at request time

**Context.** A gate must not depend on a vendor being up, a model being
unchanged, or a rate limiter's mood. None of those are properties of the change
under test.

**Decision.** Every provider declares `reaches_network`. `build_provider`
refuses to construct one with that flag set when the run is hermetic — which is
the default — and raises before the object exists.

**Why not a check at request time.** A guard that fires when the socket opens
has already allowed a caller to build the forbidden object, and whether it then
fires depends on which code path a run happens to take. Refusing construction
makes the guarantee independent of control flow.

**Consequence.** The default for `Provider.reaches_network` is `True`, so a new
provider that forgets to declare it is refused rather than admitted. The safe
default for a security flag is the restrictive one.

## ADR-006 — The judge is a provider, so model-graded checks replay

**Context.** LLM-as-judge is the part of an evaluation harness that CI usually
cannot run. It needs a model, so it is skipped, so it is never exercised, so
nobody knows whether it works. This series has already shipped one gate that was
never invoked; the lesson is that a check which has never run is not a check.

**Decision.** The judge is an ordinary `Provider`. In replay mode its answers
come from a cassette, so a model-graded case is as deterministic as an
exact-match one and runs in CI with no credential and no egress.

**Two consequences that follow.**

- The judge is asked for a **structured verdict** — `{"verdict": "...",
  "reason": "..."}` — not for prose. Parsing "yes, that looks correct" out of a
  paragraph is a second, unvalidated grader hiding inside the first.
- An **unparseable judge answer fails the case.** Not "skipped", not "passed
  with a warning". A judge that cannot be understood has not endorsed anything.

The judge's system message is fixed by this package rather than taken from the
suite. A judge prompt a suite can rewrite is a grader a suite can weaken.

## ADR-007 — Fault injection into the response is the core deliverable

**Context.** Nothing in an ordinary evaluation run checks whether the
*instrument* works. A suite of twenty cases can report a perfect score while
every one of its graders would accept an empty string, and the report looks
identical to a suite that would catch it.

**Decision.** `aievals mutate` takes responses the suite accepted, corrupts
them, and re-grades. The mutation score is the fraction of applicable
corruptions caught, and a surviving mutant is named with the case, the
corruption, and what should have been noticed.

**It is not mutation testing.** Classic mutation testing mutates the code under
test. There is no code under test here; what is being measured is the suite's
power to discriminate. The CLI verb is `mutate` because that is what people will
look for, and the docstring and the README both say what is actually mutated —
otherwise a reader who opens `mutation/` expecting `mutmut` semantics concludes
the term was misused.

**Three implementation choices.**

- **The provider is not called again.** Grading is a pure function of the
  response, so mutation runs over the responses the base run already produced. A
  meta-gate that re-ran the suite once per mutator would cost eleven times a
  full run and nobody would put it in CI.
- **A corruption that changes nothing is discarded, not scored.** Stripping
  formatting from unformatted prose returns it unchanged. Counting that as a
  survivor blames the suite for the mutator's own no-op.
- **Judge graders are excluded, visibly.** Under replay there is no recording
  for a mutated response, so a judge would raise and the mutant would be
  recorded as caught — a spurious catch that flatters the score. They are
  dropped, and the report says how many were dropped and which cases fell
  outside the gate entirely.

**A run with no applicable mutants scores 0.0, not 1.0.** An empty measurement
is not a perfect one.

## ADR-008 — The digest covers what is measured, and a stale baseline is fatal

**Context.** A baseline is a claim about a particular suite. Compare a run
against a baseline from a different suite and the answer is meaningless in a way
that looks exactly like a real result: the same case ids, plausible numbers, a
verdict nobody should act on.

**Decision.** Suites are content-addressed, and a digest mismatch is a **hard
failure** that suppresses every finding downstream of it.

**What is in the digest.** Everything that changes what is measured: the schema
version, suite name, provider selection and sampling parameters, thresholds,
default graders, and per case its id, prompt, system message and grader
specifications.

**What is not.** Prose (`description`, `rationale`, `describe`), tags, file
paths, modification times, key ordering, and the order of the cases. Getting
this wrong is unsound in one direction and useless in the other: a digest that
covers comments makes every prose edit invalidate a baseline, people start
passing `--allow-stale-baseline`, and then the check is gone.

## ADR-009 — Redaction runs over the assembled artefact, once, last

**Context.** Reports are uploaded to CI, attached to pull requests and kept for
months. Model responses can contain credentials.

**Decision.** `redact_structure` walks a *finished* object graph and every
caller is arranged so it runs last.

**Why, specifically.** An earlier project in this series redacted the inputs and
then attached a scanner excerpt derived from the raw content — which carried
exactly what the pass had removed. The same shape exists here in three places: a
grader detail quoting the response it rejected, a mutation survivor's excerpt,
and a provider error carrying whatever the server said. Redacting inputs would
have missed all three.

**It walks the structure rather than serialising and re-parsing.** A JSON
round-trip mangles quoting and redacts *keys* as well as values, producing
reports whose field names are `[REDACTED]`.

**The privacy claim is stated precisely.** With `--include-responses` off, the
full response is replaced by its length and a digest; a grader detail may still
quote a bounded fragment, because that fragment is the reason the build is red.
Claiming more than that would be false, and `tests/security/test_no_leaks.py`
pins both halves.

## ADR-010 — A static ReDoS guard, vendored rather than depended on

**Context.** A suite file may declare a `regex` grader, and a suite file is
untrusted input.

**Decision.** Patterns are checked for catastrophic backtracking **before
compilation**, using a copy of the guard written for
[mcp-developer-server](https://github.com/kogunlowo123/mcp-developer-server) at
commit `bfdb362`.

**Why before compilation.** `(a+)+b` against forty `a` characters is a single
`re.search` that does not return in any useful time, and no timeout above it
helps: `asyncio.wait_for` around `asyncio.to_thread` abandons the coroutine but
cannot stop the thread, and Python offers no way to interrupt a running match.
In CI that is a job that burns its whole time budget and reports nothing.

**Why vendored.** A four-dependency evaluation harness should not acquire a
Model Context Protocol server to obtain two hundred lines of pure static
analysis, and the two copies are free to diverge as each project's threat model
does. The origin and commit are named in the module docstring so it reads as a
deliberate copy rather than duplication nobody noticed.

**It is a heuristic.** Recognising every exponential regular expression is
undecidable. It catches the shapes that appear in practice — a quantified group
containing a quantifier, and a quantified group whose alternatives can start
with the same character — and `THREAT-MODEL.md` records what remains.

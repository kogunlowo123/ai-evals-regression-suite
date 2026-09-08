# The gate

`gate.py` is the only module that turns a measurement into a verdict. Everything
above it reports facts; everything below it renders them.

## Exit codes

| Code | Meaning | What a CI job should do |
| --- | --- | --- |
| `0` | Every gate passed | Merge |
| `1` | Usage error | Fix the command |
| `2` | **A gate failed** | Block the merge |
| `3` | The harness could not run | Investigate the runner |

`2` and `3` are separate because they call for different responses. A single
non-zero code makes a job unable to tell a regression from an outage, and teams
respond to that by retrying until it goes green.

## The rules

### `REGRESSION` — replay mode only

A case that passed at baseline and fails now. **Fatal.**

No statistics are involved. A replay run draws one deterministic completion per
case, so a flip was caused by the change under test.

### `SIGNIFICANT_REGRESSION` — sampled mode only

The paired exact McNemar test rejects "no change" *and* the movement is
downward. **Fatal.**

### `REGRESSION_NOT_SIGNIFICANT` — sampled mode only

Cases flipped, but sampling can explain it. **A warning.** Reported because the
person reading the build needs to know which cases moved.

### Why the mode split

Two rules are both obviously right — "a case flipped" and "the rate dropped
significantly" — and using both is a bug.

Under one deterministic sample per case, let `b` be the pass→fail flips and `c`
the fail→pass flips. The pass rate falls exactly when `c < b`, which requires
`b ≥ 1`, which means the per-case rule has already fired. Writing both as
independent conditions gives you a statistics module that can never be the sole
reason a build is red. See ADR-002.

### `STALE_BASELINE`

The baseline was recorded against a different suite digest. **Fatal**, and it
suppresses every finding below it: a regression count derived from the wrong
suite is worse than no count, because it looks like a result.

`--allow-stale-baseline` downgrades it to a warning and lets the rest report.

### `PASS_RATE_BELOW_MINIMUM`

Below `thresholds.min_pass_rate`, or the `--min-pass-rate` override. **Fatal.**
Applies with or without a baseline, so a first run is still gated.

### `LATENCY_BUDGET_EXCEEDED`

The p95 of per-case mean latency is over `thresholds.max_p95_latency_ms`.
**Fatal.**

Nearest-rank, not interpolated: with twenty cases the interpolated value is a
number no case actually took, and a budget should be breached by something a
reader can point at.

### `TOKEN_BUDGET_EXCEEDED` and `TOKEN_BUDGET_UNMEASURABLE`

Over `thresholds.max_total_tokens`, **or** a budget is declared and the provider
reported no usage. Both **fatal.**

The second matters. A budget silently satisfied because nothing was counted is
not a budget. The finding names the provider and says to remove the threshold or
use one that reports usage.

### `CASE_ERRORS`

A case produced no completion — a cassette miss, a timeout, a provider failure.
**Fatal.**

An error is not a pass and it is not a failure either: nothing was measured.
Failing is the only honest response, because the alternative is a green build
over a case that never ran.

### `FLAKY_CASES`

A case whose samples disagreed with each other. **A warning**, reported whatever
the sample policy decided, because the next run is a coin toss and the reader
needs to know that before trusting the number above it.

### `CASES_REMOVED`

Cases in the baseline that did not run. **A warning.**

Deleting a failing case raises the pass rate without improving anything. Removal
is often legitimate, which is why this is a warning and not a failure — but it
should never be invisible.

## Sampling

`--samples n` draws `n` completions per case. `--policy` decides how they
combine:

| Policy | A case passes when |
| --- | --- |
| `all` (default) | Every sample passed |
| `majority` | More than half passed |
| `any` | At least one passed |

`all` is the default because an intermittently correct answer is not a correct
answer. Whatever the policy, a case whose samples disagreed is reported as
flaky.

Sampling only means anything with a non-deterministic provider. Replaying a
cassette five times gives the same answer five times.

## The statistics

Only consulted under sampling. See `stats/mcnemar.py` for the argument, and
`examples/statistics_demo.py` to watch it work.

**McNemar's exact test.** Two runs of one suite are the same cases measured
twice — paired, not independent. Only the discordant pairs carry information;
under the null a discordant pair falls either way with probability 0.5, so the
count is binomial and the p-value is exact.

The exact form is valid at three discordant pairs. The chi-squared
approximation needs about twenty-five, and real suites have twenty to two
hundred cases with a handful of flips.

**One flip in twenty is not significant.** Eight is. A gate that failed on the
first would be switched off within a week.

**A significant *improvement* does not fail the build.** Both halves of the
condition are required. A build that goes red on good news gets its gate
deleted.

**Wilson intervals are reported, never gated on.** Their job is to stop 18/20
from being read as more precise than it is. The textbook normal approximation
gives a zero-width interval at 20/20 — the claim that twenty cases prove a 100%
pass rate — which is exactly the reading the interval exists to prevent.

## Reading a verdict

```console
$ aievals gate --suite suites/support.yaml --cassette cassettes/support.json \
    --baseline baselines/support.json --mutate
FAILED  support: 11/12 (91.7%) via replay
  FAIL  REGRESSION: 1 case(s) passed at baseline and fail now.
        refund-window
  warn  CASES_REMOVED: 1 case(s) in the baseline did not run.
        Removed: legacy-greeting
```

The report on stdout carries the same findings as structured data, plus the
whole run, the comparison, the interval and the test. The summary on stderr is
for a human scrolling a job log.

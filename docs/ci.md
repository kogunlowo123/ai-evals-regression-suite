# Wiring it into a pipeline

The whole point is that this runs in CI and blocks a merge. Everything below
assumes cassettes and a baseline are committed alongside the suite.

## The layout that works

```
evals/
  suites/support.yaml
  cassettes/support.json           # committed; recorded once, deliberately
  cassettes/support-judge.json     # the judge's verdicts, likewise
  baselines/support.json           # committed; the reference point
```

## GitHub Actions

```yaml
name: Evaluations

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  evals:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v5

      - uses: astral-sh/setup-uv@v7
        with: { enable-cache: true }

      - run: uv sync --locked

      # No credential, no egress: the provider that could reach one cannot be
      # constructed in a hermetic run, which is the default.
      - name: Gate
        run: |
          uv run aievals gate \
            --suite       evals/suites/support.yaml \
            --cassette    evals/cassettes/support.json \
            --judge-cassette evals/cassettes/support-judge.json \
            --judge-model gpt-4o-mini \
            --baseline    evals/baselines/support.json \
            --mutate --min-caught 1.0 \
            --json-out     reports/evals.json \
            --junit-out    reports/evals.xml \
            --markdown-out reports/evals.md

      # `always()`, so a failing gate still publishes the report explaining why.
      - name: Publish the summary
        if: always()
        run: cat reports/evals.md >> "$GITHUB_STEP_SUMMARY"

      - name: Upload the reports
        if: always()
        uses: actions/upload-artifact@v5
        with:
          name: evaluation-reports
          path: reports/
```

Two details that are easy to get wrong:

**No `continue-on-error`.** A gate that cannot fail the build is a dashboard
with extra steps.

**`if: always()` on the reporting steps only.** A red build must still publish
the report that explains itself; it must not go green because the report
published successfully.

## GitLab CI

```yaml
evals:
  image: python:3.12-slim
  before_script:
    - pip install uv && uv sync --locked
  script:
    - uv run aievals gate
        --suite evals/suites/support.yaml
        --cassette evals/cassettes/support.json
        --baseline evals/baselines/support.json
        --mutate
        --junit-out reports/evals.xml
  artifacts:
    when: always
    reports:
      junit: reports/evals.xml
```

## Interpreting the exit code

```bash
uv run aievals gate ... ; status=$?
case $status in
  0) echo "green" ;;
  2) echo "a gate failed: a regression, a threshold, or a surviving mutant" ;;
  3) echo "the harness could not run: bad suite, missing cassette, broken runner" ;;
esac
```

`2` and `3` are separate on purpose. A job that cannot tell a regression from an
outage gets retried until it goes green.

## Changing the reference point

A baseline is a claim about a particular suite, and the digest enforces that. So
the workflow after a deliberate change is:

```bash
# 1. edit the suite (a prompt, a grader, a threshold, the model)
# 2. re-record if a prompt or the model changed
aievals record --suite evals/suites/support.yaml \
  --out evals/cassettes/support.json --allow-network

# 3. re-record the baseline, and commit it in the same pull request
aievals baseline --suite evals/suites/support.yaml \
  --cassette evals/cassettes/support.json \
  --out evals/baselines/support.json \
  --note "added the store-credit expectation, see #482"
```

**Commit the baseline in the pull request that changed the suite.** A reviewer
looking at the diff sees both the new expectation and the verdicts it produced,
which is the whole point of keeping the baseline in version control.

Editing prose — a `description`, a `rationale`, a `describe` — does not
invalidate a baseline. That is deliberate: a digest that cried wolf on comment
changes would teach everyone to pass `--allow-stale-baseline`, and then the
check would be gone.

## Sampling in CI

If a suite runs against a live provider on a schedule rather than on every pull
request:

```bash
aievals gate --suite evals/suites/support.yaml \
  --baseline evals/baselines/support-live.json \
  --samples 5 --policy majority --allow-network
```

The gate switches to the paired exact test automatically because the run is
sampled. Keep this on a schedule, not on pull requests: it costs money, and its
verdict depends on a third party's uptime.

## What to gate on first

If you are adding this to an existing project, in order:

1. **`min_pass_rate` on a replay run.** Cheap, immediate, no baseline needed.
2. **A baseline, and `REGRESSION`.** The rule that catches the thing everyone
   actually fears.
3. **`--mutate --min-caught 1.0`.** Expect it to fail the first time. That is
   the finding, not a false positive — and the survivors name exactly which
   expectations to add.
4. **Budgets**, once the rest is stable. A latency budget on a replay run
   measures the cassette, so this one only means something against a live
   provider.

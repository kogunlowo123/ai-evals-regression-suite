# Suite files

A suite is a YAML or JSON document describing cases and what a correct answer to
each looks like. It is **data**: it names graders, it cannot supply them, and
nothing in it is imported, resolved or evaluated. See ADR-004.

## The whole schema

```yaml
schema_version: 1              # optional; 1 is the only value
name: support                  # required, [A-Za-z0-9][A-Za-z0-9._-]{0,63}
description: A sentence.       # optional prose, not part of the digest

provider:                      # optional; defaults to replay
  name: replay                 # a registry name
  model: gpt-4o-mini           # recorded, and part of the cassette key
  temperature: 0.0             # 0.0-2.0
  max_tokens: 1024             # 1-32000
  options: {}                  # passed through to the provider

thresholds:                    # optional; an absent bound is not checked
  min_pass_rate: 0.95          # 0.0-1.0
  max_p95_latency_ms: 4000     # > 0
  max_total_tokens: 50000      # > 0
  alpha: 0.05                  # significance level, sampled runs only

default_graders:               # applied to every case, before its own
  - type: contains_none
    describe: no boilerplate   # shown in reports when this grader fails
    required: true             # optional; default true
    params:
      values: ["As an AI language model"]

cases:                         # required, at least one
  - id: refund-window          # required, unique, [A-Za-z0-9][A-Za-z0-9._-]{0,127}
    prompt: How long do I have to request a refund?   # required, non-empty
    system: You are a support agent.                  # optional
    tags: [policy, money]                             # optional, for --tag
    rationale: >                                      # optional prose
      Why this case exists. Suites rot when nobody remembers.
    graders:
      - type: numeric_close
        params: {expected: 30, where: first}
```

Every model sets `extra="forbid"`. A mistyped key is an error, not a silently
dropped expectation — because a silently dropped expectation is a gate that
keeps passing after it stopped checking anything.

## Required and optional graders

A case passes when **every grader with `required: true` passes**. Optional
graders are run and reported but do not decide the verdict, which is how a new
expectation is introduced to an existing suite without breaking the gate on the
day it lands.

```yaml
graders:
  - type: contains_all
    params: {values: ["30 days"]}
  - type: judge                # new, watch it for a week before enforcing
    required: false
    params: {criterion: The tone is warm without being obsequious.}
```

## Default graders

`default_graders` apply to every case, before its own. The usual content is a
house rule — no refusal boilerplate, no leaked system prompt, no phrase legal
has ruled out — that would otherwise be copied into every case and drift apart.

## Tags

`--tag policy` runs only cases carrying that tag. A tag that matches nothing is
an **error**, not an empty run: an empty run passes every gate trivially, which
is the worst possible way for a filter typo to behave.

Tags are not part of the suite digest. Adding one does not invalidate a
baseline, because it does not change what any case measures.

## `describe`

A short label prefixed onto a grader's failure detail:

```yaml
- type: numeric_close
  describe: the refund window in days
  params: {expected: 30}
```

```
the refund window in days: expected 30 ± 0 (absolute), got 60, off by 30
```

It is prose, so it is not part of the digest. Editing it does not invalidate a
baseline.

## `rationale`

Why the case exists. It appears in no gate rule and no digest, and it is the
single most valuable field in the file eighteen months later, when someone is
deciding whether a failing case is a regression or an obsolete requirement.

## Content addressing

Every suite has a digest — `aievals doctor --suite <path>` prints it — and a
baseline records the digest it was made from. Comparing against a baseline from
a different suite is a hard failure.

**In the digest:** schema version, name, provider selection and sampling
parameters, thresholds, default graders, and per case its id, prompt, system
message and grader specifications.

**Not in the digest:** `description`, `rationale`, `describe`, tags, file paths,
key order, and the order of the cases themselves.

So: editing a comment is free. Changing a prompt, a grader parameter, a
threshold or the model is not — and should not be, because each of those changes
what the baseline was measuring.

## Size limits

| Limit | Value | Why |
| --- | --- | --- |
| File size | 4 MiB | Checked *before* parsing |
| Cases | 10 000 | A generated file that runs for an afternoon |
| Regex pattern | 1 000 characters | Longer is a program, not a check |
| Judge criterion | 2 000 characters | Longer is several criteria in a coat |

## What a suite file cannot do

- **Import anything.** Graders are names, resolved in a registry.
- **Construct Python objects.** `yaml.safe_load`, so `!!python/object/apply` is
  a parse error rather than a shell.
- **Carry a credential.** The HTTP provider refuses an `api_key` option and
  names the environment-variable alternative in the error.
- **Include another file.** There is no include directive, no `$ref`, and no
  schema resolution. One file is one suite.

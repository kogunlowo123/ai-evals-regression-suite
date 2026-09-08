# The meta-gate

An evaluation suite is a measuring instrument, and nothing in an ordinary run
checks whether the instrument works. A suite of twenty cases can report a
perfect score while every one of its graders would accept an empty string, and
the report looks identical to a suite that would catch it.

```console
$ aievals mutate --suite suites/support.yaml --cassette cassettes/support.json
  SURVIVED  refund-window / drop_numbers: a suite whose case turns on a figure
            must check the figure
            the suite accepted: "You can request a refund within  days of purchase."
FAIL  the mutation score 92.3% is below the required 100.0%.
```

That suite scored 12/12 against the model. It would also have scored 12/12
against a model that had forgotten the number.

## How it works

1. Run the suite normally.
2. For every case that **passed**, take the response the suite accepted.
3. Apply each **applicable** mutator to it.
4. Re-grade the corrupted response with the same graders.
5. A mutant is **caught** if the verdict flipped to failing, and **survived** if
   it did not.

The mutation score is caught ÷ applicable. `--min-caught` is the gate, and it
defaults to `1.0`.

**No extra model calls.** Grading is a pure function of the response, so this
runs over the responses the base run already produced. A meta-gate that re-ran
the suite once per mutator would cost eleven times a full run, and nobody would
put that in CI.

## What "not a mutant" means

Three things are excluded, and each is excluded for the same reason: counting
them would produce a number about something other than the suite.

**Cases that already fail.** A failing case cannot be flipped to failing.
Counting it inflates the score with cases the suite is not measuring. Reported
under `skipped`.

**Inapplicable mutators.** `corrupt_json` applied to prose produces prose. No
suite should be expected to notice, and counting it as a survivor would make
`--min-caught 1.0` unreachable and therefore ignored.

**Corruptions that change nothing.** Stripping formatting from unformatted
prose returns it unchanged. Scoring an identical "mutant" either way is a number
about the mutator, not about the suite.

**A run with no applicable mutants scores 0.0, not 1.0.** An empty measurement
is not a perfect one, and returning 1.0 would let a suite pass by being
impossible to mutate.

## Judge graders

Excluded, visibly.

A model-graded check under replay has no recording for a mutated response, so it
would raise, so the mutant would be recorded as caught — a spurious catch that
flatters the score. Instead, judge graders are dropped from mutation grading and
the report says how many were dropped. A case whose *only* required grader is a
judge is skipped entirely and listed by name, because "this case is outside the
meta-gate" is something a reader has to be told rather than left to infer.

## The mutators

`aievals list mutators` prints them. `--set core` (the default), `--set
extended`, `--set all`, or `--mutator <name>` repeated.

### Core

Corruptions any competent suite should catch. This is the set `--min-caught
1.0` is meant for.

| Mutator | What it does | Applicable when |
| --- | --- | --- |
| `empty` | Replaces the response with `""` | always |
| `refusal` | Replaces it with a polite refusal | always |
| `truncate` | Keeps the first 30% | response ≥ 40 characters |
| `drop_numbers` | Removes every digit | the response has a digit |
| `corrupt_json` | Removes the final character | the response parses as JSON |

### Extended

Diagnostic. These surface real gaps, and a suite that does not catch them is not
necessarily wrong — it may simply not be about that property.

| Mutator | What it does |
| --- | --- |
| `swap_numbers` | Replaces figures with wrong ones of the same shape |
| `negate` | Negates the first assertion |
| `shuffle_sentences` | Reorders the sentences |
| `boilerplate` | Prefixes model boilerplate |
| `inject_instruction` | Appends an instruction addressed to the reader |
| `strip_formatting` | Removes fences, list markers and headings |

Run the extended set to find out what your suite is blind to; gate on the core
set. This repository's own example scores 100% on core and does not on extended,
and `--set extended` names exactly which properties it does not check. That is a
more useful thing to publish than a rigged 100%.

## Determinism

Mutators take a seed, derived from `(seed, case id, mutator name)`, so the same
pair corrupts the same way on every machine and in every run. A quality gate
whose value moves on its own is one people learn to re-run.

## Reading a survivor

```
SURVIVED  order-reference / drop_numbers: a suite whose case turns on a figure
          must check the figure
          the suite accepted: I have found the order and it is awaiting dispatch...
```

Three parts: the **case**, the **corruption**, and **what a suite ought to have
noticed**. The excerpt shows what was accepted, so the fix is usually obvious —
here, add a grader that checks the order reference survived.

## Wiring it in

```bash
# standalone, its own exit code
aievals mutate --suite suites/support.yaml --cassette cassettes/support.json

# alongside the gate, in one run, in one report
aievals gate --suite suites/support.yaml --cassette cassettes/support.json \
  --baseline baselines/support.json --mutate --min-caught 1.0
```

## What it does not tell you

**A high mutation score does not mean the suite is right.** It means the suite
would notice a corrupted answer. Twenty cases about the wrong requirement score
perfectly and measure nothing that matters. Choosing what to evaluate is still
the hard part; this only makes sure the measurement holds.

## Why not "mutation testing"

Classic mutation testing mutates the *code under test*. There is no code under
test here — what is being measured is the suite's power to discriminate, and the
thing being corrupted is the graded artefact. The CLI verb is `mutate` because
that is what people look for, but a reader who opens `mutation/` expecting
`mutmut` semantics should find this paragraph before they conclude the term was
misused.

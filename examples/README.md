# The worked example

A real suite, real recordings, a real baseline, and three runnable scripts.
Everything here works offline with no credential.

## Where the answers come from

**`support-answers.yaml` is authored. It is not captured from a vendor.**

Saying so plainly matters: the files next to it are cassettes in the ordinary
format, and someone reading `support-cassette.json` could reasonably assume it
came from a real model. It did not. The answers were written by hand so that
this repository has a complete end-to-end example — record, baseline, gate,
meta-gate — that anyone can run without an account, a card, or egress.

`scripts/build-example-cassette.py` turns them into cassettes, so the example
exercises the same replay path a real recording would, and CI regenerates them
and fails if they have drifted from the suite.

To record real ones for your own suite:

```bash
aievals record --suite your-suite.yaml --out your-cassette.json --allow-network
```

## The files

| File | What it is |
| --- | --- |
| `support.yaml` | Seven cases, exercising every deterministic grader and the judge |
| `support-answers.yaml` | The authored answers, and the judge's verdicts |
| `support-cassette.json` | Built from the answers; what `--cassette` replays |
| `support-judge-cassette.json` | The judge's recorded verdicts |
| `support-baseline.json` | The reference point the gate compares against |

## Running it

```bash
python tasks.py gate       # run, compare, meta-gate — the CI command
python tasks.py mutate     # the meta-gate alone; 26/26 caught
python tasks.py doctor     # what this installation would do
```

Or in full:

```bash
uv run aievals gate \
  --suite examples/support.yaml \
  --cassette examples/support-cassette.json \
  --judge-cassette examples/support-judge-cassette.json \
  --judge-model demo-judge \
  --baseline examples/support-baseline.json \
  --mutate
```

## The scripts

**`quickstart.py`** — run a suite, record a baseline, change one answer, watch
the gate fail with exit code 2. The whole loop in one screen.

**`mutation_demo.py`** — two suites that both score 100% against the same
response, and only one of them is measuring anything. This is the argument for
the meta-gate in thirty lines of output.

**`statistics_demo.py`** — why one flipped case in twenty is not a regression
and eight are, why the exact test rather than chi-squared, and why 20/20 is not
a proven 100%.

All three run in about a second and are executed in CI, because an example that
has stopped working is a README that lies.

## One thing the example shows about itself

`support.yaml` has a grader with this comment above it:

```yaml
# Added because the meta-gate found the hole: without something from the
# end of the answer, `aievals mutate` could cut the response to 30% and
# the case still passed.
```

That is not decoration. The suite scored 100% on the model and the meta-gate
caught a corruption it would have accepted. It is the clearest single argument
for the tool, and it happened during development rather than being staged.

Run `python tasks.py mutate-extended` to see what this suite is *still* blind
to. The core set is green; the extended set names real gaps, and publishing that
honestly is more useful than a rigged 100%.

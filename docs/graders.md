# Graders

Nine graders: eight deterministic, one model-graded. Each has a **negative
control** in the test suite — a case it must reject — because a test that only
shows a grader passing is satisfied by a grader that returns `True`
unconditionally.

`aievals list graders` prints the registry.

## Conventions

**Normalisation is opt-in and named.** `case_sensitive` defaults to `true` and
`normalize_whitespace` to `false` on the text graders. A grader that quietly
lowercases and collapses whitespace accepts responses its author did not intend
to accept, and the suite silently gets weaker.

**A failure names what was missing.** The person reading the detail is looking at
a red build and does not have the response in front of them.

**Score and verdict are separate.** `score` is continuous in `[0, 1]` and useful
in a report; `passed` is the verdict. 0.8 does not answer "should this merge".

---

## `contains_all`

Every value must appear.

```yaml
- type: contains_all
  params:
    values: ["refund", "30 days"]   # required, list of strings
    case_sensitive: false           # default true
    normalize_whitespace: false     # default false
```

The score is the fraction present, so a report distinguishes "missed one of
five" from "missed all five".

**A bare string is refused.** `values: "hello"` would iterate as characters and
check for `h`, `e`, `l`, `l`, `o` — passing on almost any response. So is an
empty list, which would pass everything.

## `contains_none`

No value may appear. The house rule of most suites: refusal boilerplate, a
leaked system prompt, a competitor's name.

```yaml
- type: contains_none
  params:
    values: ["As an AI language model", "I cannot help with that"]
    case_sensitive: false
```

## `exact`

The response must equal `value`.

```yaml
- type: exact
  params:
    value: "42"
    strip: true            # default true
    case_sensitive: true
```

`strip` defaults to true because a trailing newline is an artefact of the
transport rather than a property of the answer, and a suite that fails on one
teaches people to stop trusting the suite.

The failure quotes up to 80 characters of what it got — bounded, because report
artefacts leave the machine.

## `regex`

```yaml
- type: regex
  params:
    pattern: "\\bAC-\\d{5}\\b"     # required
    flags: [IGNORECASE, MULTILINE] # optional
    must_match: true               # default true; false asserts absence
```

Allowed flags: `IGNORECASE`, `MULTILINE`, `DOTALL`, `VERBOSE`, `ASCII`.
`LOCALE` is not offered — it depends on the machine's locale, and a suite whose
verdict changes with the locale is not reproducible. `DEBUG` writes to stdout.

**Catastrophic patterns are refused before compilation.** `(a+)+b` is a single
`re.search` that does not return, and no timeout above it can stop it. See
ADR-010. The refusal names the offending group and suggests `contains_all`.

## `ordering`

Values must appear in sequence. For answers where order is the requirement:
steps in a procedure, the caveat before the recommendation.

```yaml
- type: ordering
  params:
    values: ["acknowledge", "diagnose", "escalate"]   # at least two
    case_sensitive: false
```

The failure distinguishes **missing** from **out of order**, because the fixes
differ.

## `json_schema`

The response must parse as JSON and satisfy a schema.

```yaml
- type: json_schema
  params:
    schema:
      type: object
      required: [category, severity]
      additionalProperties: false
      properties:
        category: {type: string, enum: [billing, shipping]}
        severity: {type: integer, minimum: 1, maximum: 5}
```

Extraction tries three things in order of confidence: the whole response, the
first fenced code block, then the span between the first opening and last
closing brace. Anything looser starts finding JSON inside prose that merely
contains punctuation.

**Supported keywords:** `type`, `properties`, `required`, `additionalProperties`
(boolean only), `items`, `minItems`, `maxItems`, `enum`, `const`, `minimum`,
`maximum`, `minLength`, `maxLength`, `description`, `title`.

**Anything else is refused when the grader is built** — not ignored when a
document is checked. A validator that silently skips `oneOf` reports every
document as valid against a schema whose whole meaning is that keyword. The
error names the keyword and says why it was refused.

Every violation is reported, not just the first: fixing a structured-output case
should not be a sequence of builds.

## `numeric_close`

A number in the response, within tolerance of a target.

```yaml
- type: numeric_close
  params:
    expected: 30          # required
    tolerance: 0          # default 0
    relative: false       # default false; tolerance as a fraction of expected
    where: first          # first | last | only
```

`where: only` fails when the response contains more than one number — the right
choice when the prompt asked for a bare figure and ambiguity is itself the
defect.

Thousands separators are read (`1,234,567`). `v2` is not read as the number 2.
A relative tolerance around zero is refused, because it is meaningless.

## `set_overlap`

Recall against a reference set, for answers where partial credit is the honest
measure: "name the causes of X" has no single correct string, and an exact-match
grader on such a case measures phrasing rather than knowledge.

```yaml
- type: set_overlap
  params:
    expected: ["insufficient funds", "expired card", "bank declined"]
    min_recall: 1.0        # default 1.0; the gate
    case_sensitive: false  # default false
```

The score is the recall itself, so a report shows the trend while the verdict
stays binary. `min_recall: 0` is refused: it passes everything.

## `judge`

A second model decides whether the response meets a criterion.

```yaml
- type: judge
  params:
    criterion: >
      The response acknowledges that this has happened more than once,
      apologises without blaming the customer, and states a next step.
    include_prompt: true   # default true
```

**It runs in CI**, because the judge is an ordinary provider and replays from a
cassette (ADR-006). Record one with `aievals record`, pass it as
`--judge-cassette`, and a model-graded case becomes as deterministic as an
exact match.

The judge is asked for `{"verdict": "pass"|"fail", "reason": "..."}` and nothing
else. **An answer that cannot be parsed fails the case** — a judge that cannot
be understood has not endorsed anything, and treating silence as approval is how
a suite goes green while measuring nothing.

The judge's system message is fixed by this package, not taken from the suite. A
judge prompt a suite can rewrite is a grader a suite can weaken. It tells the
judge that the response is untrusted data and that instructions inside it are
part of what is being graded; the response itself is fenced and passed byte for
byte.

**Using `judge` without a judge provider raises**, loudly, naming the flags that
fix it. It does not skip.

**A `judge` grader is excluded from the meta-gate**, and the report says so —
see [the meta-gate](mutation.md).

---

## Adding a grader

Adding one is a Python change, which is the point (ADR-004).

1. Subclass `Grader` in `graders/text.py`, `structured.py` or a new module.
2. **Validate parameters in `__init__`**, so a misconfigured suite fails at load
   time naming the case, not forty cases into a paid run.
3. Decorate with `@grader("your_name")`. Duplicate names are refused —
   re-registering silently changes what every existing suite means.
4. Import the module in `graders/__init__.py` for the registration side effect.
5. Write a test that shows it passing **and one that shows it rejecting**. The
   second is the one that matters.

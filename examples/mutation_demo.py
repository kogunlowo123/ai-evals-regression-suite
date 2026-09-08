#!/usr/bin/env python
"""Two suites that both score 100%, and only one of them is measuring anything.

This is the argument for the meta-gate in thirty lines of output. Both suites
below pass every case against the same response. Corrupt that response and one
suite notices; the other does not, and nothing in an ordinary evaluation report
would ever tell you which is which.

    python examples/mutation_demo.py
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aievals.mutation import MutationHarness
from aievals.providers.offline import ScriptedProvider
from aievals.runner import Runner
from aievals.suite import parse_suite

ANSWER = (
    "You can request a refund within 30 days of purchase, counted from the "
    "delivery date. After 30 days we can offer store credit instead, and a "
    "supervisor can review anything unusual."
)

# Checks the figure and both halves of the answer.
STRONG = textwrap.dedent(
    """
    name: strong
    provider: {name: scripted}
    cases:
      - id: refund-window
        prompt: How long do I have to request a refund?
        graders:
          - type: numeric_close
            params: {expected: 30, where: first}
          - type: contains_all
            params: {values: ["refund", "store credit"], case_sensitive: false}
    """
)

# Checks that a phrase nobody would ever write is absent. Perfectly green,
# perfectly useless — and this shape is extremely common in real suites.
WEAK = textwrap.dedent(
    """
    name: weak
    provider: {name: scripted}
    cases:
      - id: refund-window
        prompt: How long do I have to request a refund?
        graders:
          - type: contains_none
            params: {values: ["absolutely categorically never"]}
    """
)


async def report_on(label: str, document: str) -> None:
    suite = parse_suite(document, origin=label)
    provider = ScriptedProvider({"responses": {suite.cases[0].prompt: ANSWER}})
    run = await Runner(provider).run(suite)

    print(f"--- {label}")
    print(f"    ordinary run: {run.passed}/{run.total} passed ({run.pass_rate:.0%})")

    mutation = await MutationHarness().analyse(suite, run)
    print(f"    meta-gate:    {mutation.summary()}")
    for mutant in mutation.survivors:
        print(f"      SURVIVED  {mutant.mutator}: {mutant.expectation}")
        print(f"                the suite accepted: {mutant.excerpt}")
    if not mutation.survivors:
        for name, row in mutation.by_mutator().items():
            print(f"      caught    {name} ({row['caught']}/{row['total']})")
    print()


async def main() -> int:
    print("Both suites pass every case against the same response.\n")
    await report_on("a suite with real expectations", STRONG)
    await report_on("a suite that checks nothing useful", WEAK)
    print("An evaluation report would show 100% for both.")
    print("`aievals mutate` is what tells them apart, and it exits 2 on the second.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

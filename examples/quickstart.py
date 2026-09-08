#!/usr/bin/env python
"""Run a suite, record a baseline, and watch a regression fail the gate.

Runs offline in about a second: no credential, no network, no recording needed.
The provider is ``scripted``, which answers from a table in this file, so the
whole loop — run, baseline, regress, gate — is visible in one screen.

    python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aievals.baseline import Baseline, compare
from aievals.gate import evaluate
from aievals.providers.offline import ScriptedProvider
from aievals.runner import Runner
from aievals.suite import parse_suite

SUITE = textwrap.dedent(
    """
    name: quickstart
    description: Three things a support assistant has to get right.
    provider:
      name: scripted
      model: demo
    thresholds:
      min_pass_rate: 1.0
    cases:
      - id: refund-window
        prompt: How long do I have to request a refund?
        graders:
          - type: numeric_close
            params: {expected: 30, where: first}
          - type: contains_all
            params: {values: ["refund"], case_sensitive: false}
      - id: no-boilerplate
        prompt: Is my order on its way?
        graders:
          - type: contains_none
            params: {values: ["As an AI language model"], case_sensitive: false}
      - id: structured
        prompt: Classify this ticket as JSON.
        graders:
          - type: json_schema
            params:
              schema:
                type: object
                required: [category]
                properties:
                  category: {type: string, enum: [billing, shipping]}
    """
)

GOOD = {
    "How long do I have to request a refund?": (
        "You can request a refund within 30 days of purchase."
    ),
    "Is my order on its way?": "Yes — it left the warehouse this morning.",
    "Classify this ticket as JSON.": '{"category": "billing"}',
}

# One answer changed: the window is now wrong. Everything else is untouched.
REGRESSED = {**GOOD}
REGRESSED["How long do I have to request a refund?"] = (
    "You can request a refund within 60 days of purchase."
)


async def main() -> int:
    suite = parse_suite(SUITE, origin="quickstart")

    print("1. Run the suite against answers that are correct.")
    healthy = await Runner(ScriptedProvider({"responses": GOOD})).run(suite)
    print(f"   {healthy.passed}/{healthy.total} passed ({healthy.pass_rate:.0%})")
    print(f"   suite digest: {healthy.suite_digest[:26]}...")

    print("\n2. Record that as the baseline.")
    baseline = Baseline.from_run(healthy, note="the quickstart example")
    print(f"   recorded {len(baseline.outcomes())} verdicts at {baseline.pass_rate:.0%}")

    print("\n3. Change one answer and run again.")
    regressed = await Runner(ScriptedProvider({"responses": REGRESSED})).run(suite)
    print(f"   {regressed.passed}/{regressed.total} passed ({regressed.pass_rate:.0%})")

    print("\n4. Gate the second run against the baseline.")
    report = evaluate(regressed, suite, comparison=compare(baseline, regressed))
    for finding in report.findings:
        print(finding.render())
    print(f"\n   verdict: {'PASSED' if report.passed else 'FAILED'}")
    print(f"   exit code a CI job would see: {report.exit_code}")

    print("\nThe point: 'the pass rate went from 100% to 67%' is a number.")
    print("'refund-window passed at baseline and fails now' stops the merge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

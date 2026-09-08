#!/usr/bin/env python
"""Why the gate uses a paired exact test, and why only under sampling.

Three things this prints, each of which is a decision in `aievals.gate`:

1. On a twenty-case suite, one flipped case is a coin landing once. The exact
   test says so; a gate that failed on it would be turned off within a week.
2. Eight flipped cases in the same suite is not. The same test says that too.
3. The exact test disagrees with the chi-squared approximation exactly where
   evaluation suites live — a handful of discordant pairs.

    python examples/statistics_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aievals.stats import binomial_two_sided_p, mcnemar, wilson_interval

CASES = 20


def outcomes(failing: int) -> dict[str, bool]:
    """Twenty cases, the first *failing* of which fail."""
    return {f"case-{index:02d}": index >= failing for index in range(CASES)}


def show(label: str, before: dict[str, bool], after: dict[str, bool]) -> None:
    result = mcnemar(before, after, alpha=0.05)
    verdict = "FAILS THE BUILD" if result.significant_regression else "passes"
    print(f"  {label}")
    print(f"    {result.summary()}")
    print(f"    -> {verdict}\n")


def main() -> int:
    healthy = outcomes(0)

    print("A twenty-case suite, all passing, compared against itself after a change.\n")
    show("one case flipped to failing", healthy, outcomes(1))
    show("three cases flipped", healthy, outcomes(3))
    show("eight cases flipped", healthy, outcomes(8))

    print("The same suite improving is not a build failure:\n")
    show("eight cases fixed", outcomes(8), healthy)

    print("Why the exact form rather than chi-squared:\n")
    print("    discordant pairs   exact p     usable?")
    for pairs in (1, 3, 5, 6, 10, 25):
        exact = binomial_two_sided_p(pairs, pairs)
        note = "chi-squared is unreliable below about 25" if pairs < 25 else "both agree here"
        print(f"    {pairs:>3} all one way    {exact:.4f}      {note}")

    print("\nAnd why a pass rate is reported with an interval:\n")
    for passed, total in ((18, 20), (20, 20), (180, 200)):
        print(f"    {wilson_interval(passed, total).summary()}")
    print("\n    20/20 is not a proven 100%: the interval says so, a bare number does not.")

    print("\nNone of this is consulted under replay. One deterministic sample per")
    print("case means a flip was caused by the change, and a p-value over that")
    print("is decoration. See the mode split in aievals/gate.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

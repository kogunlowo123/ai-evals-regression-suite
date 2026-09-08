"""The paired significance test, in its exact form.

**Why a paired test at all.** Two runs of the same suite are not two independent
samples. They are the *same cases*, measured twice. Comparing the two pass rates
with a two-proportion z-test — which is what most harnesses reach for — assumes
independence that is not there, and it wastes the pairing: the information about
a regression is in which cases changed, not in how many passed overall.

**Why the exact form.** McNemar's test looks only at the discordant pairs: ``b``
cases that passed at baseline and now fail, and ``c`` that failed and now pass.
Under the null hypothesis that nothing changed, a discordant pair is equally
likely to fall either way, so ``b`` is binomial with ``n = b + c`` and ``p =
0.5``. That is an exact statement, computable with integer arithmetic, and valid
at ``n = 3`` — which matters, because a real evaluation suite has twenty to two
hundred cases and a handful of flips. The chi-squared approximation to the same
test is only trustworthy from about ``b + c ≥ 25``, a threshold most suites
never reach.

**Where this is used, and where it is not.** Only under sampling. A replay run
draws one deterministic completion per case, so a case that flips has flipped
because the code changed — a p-value over that is theatre, and the gate treats
any flip as a regression without consulting this module. See
:mod:`aievals.gate`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class McNemarResult:
    """The outcome of a paired comparison."""

    #: Passed before, fails now.
    regressions: int
    #: Failed before, passes now.
    improvements: int
    #: Two-sided exact p-value.
    p_value: float
    #: The significance level it was compared against.
    alpha: float

    @property
    def discordant(self) -> int:
        """How many cases changed verdict in either direction."""
        return self.regressions + self.improvements

    @property
    def significant(self) -> bool:
        """Whether the change is larger than chance at *alpha*."""
        return self.p_value <= self.alpha

    @property
    def direction(self) -> str:
        """``"worse"``, ``"better"`` or ``"unchanged"``."""
        if self.regressions > self.improvements:
            return "worse"
        if self.improvements > self.regressions:
            return "better"
        return "unchanged"

    @property
    def significant_regression(self) -> bool:
        """The condition a sampled gate fails on.

        Both halves are required. A significant change in the *improving*
        direction is good news, and a build that fails on good news gets its
        gate removed within a week.
        """
        return self.significant and self.direction == "worse"

    def summary(self) -> str:
        """One line, for a terminal."""
        if self.discordant == 0:
            return "no case changed verdict"
        return (
            f"{self.regressions} regressed, {self.improvements} improved, "
            f"p = {self.p_value:.4f} against alpha {self.alpha:g} "
            f"({'significant' if self.significant else 'not significant'}, {self.direction})"
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "test": "mcnemar_exact",
            "regressions": self.regressions,
            "improvements": self.improvements,
            "discordant": self.discordant,
            "p_value": round(self.p_value, 8),
            "alpha": self.alpha,
            "significant": self.significant,
            "direction": self.direction,
        }


def binomial_two_sided_p(successes: int, trials: int) -> float:
    """Two-sided exact binomial p-value against ``p = 0.5``.

    Computed as ``2 * P(X <= min(successes, trials - successes))`` and clamped to
    1. The symmetric shortcut is valid precisely because ``p = 0.5`` makes the
    distribution symmetric; it would be wrong for any other null.

    Integer arithmetic throughout, converted to a float once at the end, so no
    accumulation of rounding error and no underflow at large ``trials``.
    """
    if trials < 0:
        raise ValueError("trials must not be negative")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    if trials == 0:
        # No case changed verdict. There is nothing to test, and the honest
        # answer is "no evidence of a change", which is p = 1.
        return 1.0

    tail = min(successes, trials - successes)
    cumulative: int = sum(math.comb(trials, k) for k in range(tail + 1))
    # `1 << trials` rather than `2 ** trials`: identical for a non-negative
    # exponent, exact, and typed as an int — `int ** int` is `Any` to a type
    # checker, because a negative exponent would give a float.
    outcomes: int = 1 << trials
    return min(1.0, 2.0 * cumulative / outcomes)


def mcnemar(
    before: dict[str, bool],
    after: dict[str, bool],
    *,
    alpha: float = 0.05,
) -> McNemarResult:
    """Compare two sets of per-case verdicts.

    Only cases present in **both** mappings are counted. A case that was added
    or removed is not a pair, and folding it in either direction would put a
    suite edit into the significance of a code change. Callers that care about
    added and removed cases handle them separately — see
    :func:`aievals.baseline.compare.compare_runs`.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")

    shared = set(before) & set(after)
    regressions = sum(1 for case_id in shared if before[case_id] and not after[case_id])
    improvements = sum(1 for case_id in shared if not before[case_id] and after[case_id])
    p_value = binomial_two_sided_p(regressions, regressions + improvements)
    return McNemarResult(
        regressions=regressions,
        improvements=improvements,
        p_value=p_value,
        alpha=alpha,
    )

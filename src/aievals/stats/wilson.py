"""A confidence interval for a pass rate.

Reported, not gated on. Its job is to stop a number from being read as more
precise than it is: "18/20 = 90%" invites a comparison with last week's 85%,
and the interval says that on twenty cases those two numbers are the same
measurement.

The Wilson score interval rather than the textbook normal approximation, for
the reason the textbook interval fails exactly where evaluation suites live: at
a proportion near 0 or 1, ``p_hat +/- z * sqrt(p_hat (1 - p_hat) / n)`` produces
bounds outside ``[0, 1]``, and at ``p_hat = 1`` it produces a zero-width interval
— the claim that a suite passing 20 of 20 has proven a 100% pass rate. Wilson's
interval is derived by inverting the score test rather than assuming normality,
stays inside the unit interval by construction, and gives a sensible upper
bound at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist
from typing import Any


@dataclass(frozen=True, slots=True)
class Interval:
    """A proportion with a confidence interval around it."""

    point: float
    low: float
    high: float
    confidence: float
    successes: int
    trials: int

    @property
    def width(self) -> float:
        """How wide the interval is."""
        return self.high - self.low

    def summary(self) -> str:
        """One line, for a terminal."""
        percent = round(self.confidence * 100)
        return (
            f"{self.successes}/{self.trials} = {self.point:.1%} "
            f"({percent}% CI {self.low:.1%} to {self.high:.1%})"
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise for a report."""
        return {
            "point": round(self.point, 6),
            "low": round(self.low, 6),
            "high": round(self.high, 6),
            "confidence": self.confidence,
            "successes": self.successes,
            "trials": self.trials,
        }


def wilson_interval(successes: int, trials: int, *, confidence: float = 0.95) -> Interval:
    """Return the Wilson score interval for *successes* out of *trials*."""
    if trials < 0:
        raise ValueError("trials must not be negative")
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")

    if trials == 0:
        # Nothing was measured. The honest interval is the whole unit interval.
        return Interval(point=0.0, low=0.0, high=1.0, confidence=confidence, successes=0, trials=0)

    z = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    n = float(trials)
    proportion = successes / n
    denominator = 1.0 + z * z / n
    centre = (proportion + z * z / (2 * n)) / denominator
    spread = (z / denominator) * ((proportion * (1 - proportion) / n + z * z / (4 * n * n)) ** 0.5)
    return Interval(
        point=proportion,
        low=max(0.0, centre - spread),
        high=min(1.0, centre + spread),
        confidence=confidence,
        successes=successes,
        trials=trials,
    )

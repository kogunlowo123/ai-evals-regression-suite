"""Statistics, used in exactly one place and deliberately small.

Under replay a comparison is deterministic and no test is needed. Under
sampling a flip can be noise, and that is the only situation in which anything
here is consulted. See :mod:`aievals.stats.mcnemar` for why the test is paired
and exact.
"""

from __future__ import annotations

from aievals.stats.mcnemar import McNemarResult, binomial_two_sided_p, mcnemar
from aievals.stats.wilson import Interval, wilson_interval

__all__ = [
    "Interval",
    "McNemarResult",
    "binomial_two_sided_p",
    "mcnemar",
    "wilson_interval",
]

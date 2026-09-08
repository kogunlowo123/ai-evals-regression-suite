"""Baselines, and comparing a run against one."""

from __future__ import annotations

from aievals.baseline.compare import Comparison, compare
from aievals.baseline.snapshot import BASELINE_VERSION, Baseline

__all__ = ["BASELINE_VERSION", "Baseline", "Comparison", "compare"]

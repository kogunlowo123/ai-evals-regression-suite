"""Negative controls for the gate itself.

Every other test in this repository checks that the harness reports correctly.
These check the thing that actually matters: **that a build goes red when it
should.** One requirement is broken at a time and the corresponding gate finding
is asserted to fire — the same discipline the meta-gate applies to a suite,
applied to the gate.

A gate that has only ever been run against a healthy repository proves nothing
about whether it would notice an unhealthy one. If this file is deleted, the
claim on the front of the README stops being supported by anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aievals.errors import EXIT_GATE_FAILED, EXIT_OK
from tests.conftest import EXAMPLES

pytestmark = pytest.mark.meta

SUITE = EXAMPLES / "support.yaml"
CASSETTE = EXAMPLES / "support-cassette.json"
JUDGE = EXAMPLES / "support-judge-cassette.json"
BASELINE = EXAMPLES / "support-baseline.json"


def gate(*extra: str, suite: Path = SUITE, cassette: Path = CASSETTE, baseline: Path = BASELINE):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "aievals",
            "gate",
            "--suite",
            str(suite),
            "--cassette",
            str(cassette),
            "--judge-cassette",
            str(JUDGE),
            "--judge-model",
            "demo-judge",
            "--baseline",
            str(baseline),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def cassette_with(replacements: dict[str, str], destination: Path) -> Path:
    """Write a copy of the example cassette with some answers replaced."""
    payload = json.loads(CASSETTE.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        for marker, replacement in replacements.items():
            if marker in entry["request"]["prompt"]:
                entry["completion"]["text"] = replacement
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


class TestTheHealthyRepositoryIsGreen:
    def test_the_shipped_example_passes_every_gate(self):
        # The control for every test below: without it, a gate that fails on
        # everything would satisfy the whole file.
        result = gate("--mutate")
        assert result.returncode == EXIT_OK, result.stderr


class TestBreakingOneRequirementTurnsTheBuildRed:
    def test_a_wrong_figure_is_caught(self, tmp_path: Path):
        broken = cassette_with(
            {"refund": "You can request a refund within 60 days. Store credit after that."},
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "REGRESSION" in result.stderr
        assert "refund-window" in result.stderr

    def test_a_lost_order_reference_is_caught(self, tmp_path: Path):
        broken = cassette_with(
            {"Confirm you have found order": "I found your order; it is awaiting dispatch."},
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "order-reference" in result.stderr

    def test_prose_where_json_was_required_is_caught(self, tmp_path: Path):
        broken = cassette_with(
            {"Classify this ticket": "This is a billing issue of severity 3; please escalate."},
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "structured-triage" in result.stderr

    def test_steps_in_the_wrong_order_are_caught(self, tmp_path: Path):
        broken = cassette_with(
            {
                "payment failed twice": (
                    "Escalate to the payments team immediately. Then diagnose the "
                    "decline codes, and acknowledge the customer afterwards."
                )
            },
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "escalation-order" in result.stderr

    def test_a_missing_documented_cause_is_caught(self, tmp_path: Path):
        broken = cassette_with(
            {"usual causes": "Usually insufficient funds or an expired card."},
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "outage-causes" in result.stderr

    def test_an_invented_policy_exception_is_caught(self, tmp_path: Path):
        broken = cassette_with(
            {
                "after two years": (
                    "Our policy covers 30 days, but I can make an exception for "
                    "you and a supervisor need not be involved."
                )
            },
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED
        assert "refuses-to-invent-policy" in result.stderr

    def test_model_boilerplate_is_caught_by_the_house_rule(self, tmp_path: Path):
        broken = cassette_with(
            {
                "refund": (
                    "As an AI language model, I can tell you the window is 30 days "
                    "and store credit applies after that."
                )
            },
            tmp_path / "c.json",
        )
        result = gate(cassette=broken)
        assert result.returncode == EXIT_GATE_FAILED

    def test_a_judge_rejection_is_caught(self, tmp_path: Path):
        # The model-graded case, replayed. Rewrite the judge's verdict rather
        # than the response, so the check under test is the judge path itself.
        payload = json.loads(JUDGE.read_text(encoding="utf-8"))
        for entry in payload["entries"]:
            entry["completion"]["text"] = json.dumps(
                {"verdict": "fail", "reason": "it blames the bank"}
            )
        judge = tmp_path / "j.json"
        judge.write_text(json.dumps(payload), encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aievals",
                "gate",
                "--suite",
                str(SUITE),
                "--cassette",
                str(CASSETTE),
                "--judge-cassette",
                str(judge),
                "--judge-model",
                "demo-judge",
                "--baseline",
                str(BASELINE),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == EXIT_GATE_FAILED
        assert "tone-under-complaint" in result.stderr


class TestBreakingTheMeasurementTurnsTheBuildRed:
    def test_a_baseline_from_a_different_suite_is_caught(self, tmp_path: Path):
        edited = tmp_path / "edited.yaml"
        edited.write_text(
            SUITE.read_text(encoding="utf-8").replace("expected: 30", "expected: 31"),
            encoding="utf-8",
        )
        result = gate(suite=edited)
        assert result.returncode == EXIT_GATE_FAILED
        assert "STALE_BASELINE" in result.stderr

    def test_a_weakened_suite_is_caught_by_the_meta_gate(self, tmp_path: Path):
        # Delete the graders from one case, leaving its prompt byte-identical
        # so the recording still matches. This is a change that *raises* the
        # pass rate and leaves every other signal green: the case still runs,
        # still passes, and now checks nothing.
        text = SUITE.read_text(encoding="utf-8")
        start = text.index("  - id: order-reference")
        end = text.index("  - id: outage-causes")
        block = text[start:end]
        kept = block[: block.index("    graders:")]
        suite = tmp_path / "weak.yaml"
        suite.write_text(text[:start] + kept + text[end:], encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aievals",
                "mutate",
                "--suite",
                str(suite),
                "--cassette",
                str(CASSETTE),
                "--judge-cassette",
                str(JUDGE),
                "--judge-model",
                "demo-judge",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == EXIT_GATE_FAILED
        assert "SURVIVED  order-reference" in result.stderr

    def test_a_missing_recording_is_an_error_not_a_pass(self, tmp_path: Path):
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"cassette_version": 1, "entries": []}), encoding="utf-8")
        result = gate(cassette=empty)
        # Every case errors, so the run cannot be a pass. Exit 2, and the
        # finding says nothing was measured rather than that everything failed.
        assert result.returncode == EXIT_GATE_FAILED
        assert "CASE_ERRORS" in result.stderr

    def test_a_removed_case_is_reported_even_though_it_raises_the_rate(self, tmp_path: Path):
        text = SUITE.read_text(encoding="utf-8")
        trimmed = text[: text.index("  - id: tone-under-complaint")]
        suite = tmp_path / "trimmed.yaml"
        suite.write_text(trimmed, encoding="utf-8")
        result = gate("--allow-stale-baseline", suite=suite)
        assert "CASES_REMOVED" in result.stderr

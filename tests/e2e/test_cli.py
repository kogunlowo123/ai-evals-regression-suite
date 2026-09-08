"""The command line, driven as a real process.

These run ``aievals`` through ``python -m`` so that argument parsing, exit
codes, the stdout/stderr split and the shipped example are all exercised
together. A library test cannot show that ``echo $?`` is 2.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from aievals.errors import EXIT_ERROR, EXIT_GATE_FAILED, EXIT_OK
from tests.conftest import EXAMPLES

pytestmark = pytest.mark.e2e


def aievals(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aievals", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=180,
    )


EXAMPLE = (
    "--suite",
    str(EXAMPLES / "support.yaml"),
    "--cassette",
    str(EXAMPLES / "support-cassette.json"),
    "--judge-cassette",
    str(EXAMPLES / "support-judge-cassette.json"),
    "--judge-model",
    "demo-judge",
)


class TestBasics:
    def test_version_is_reported(self):
        result = aievals("--version")
        assert result.returncode == EXIT_OK
        assert result.stdout.startswith("aievals ")

    def test_no_subcommand_is_a_usage_error(self):
        assert aievals().returncode != EXIT_OK

    def test_doctor_reports_the_registries_and_self_checks(self):
        result = aievals("doctor")
        assert result.returncode == EXIT_OK
        assert "self-check      ok" in result.stdout
        assert "contains_all" in result.stdout

    def test_doctor_can_inspect_a_suite(self):
        result = aievals("doctor", "--suite", str(EXAMPLES / "support.yaml"))
        assert result.returncode == EXIT_OK
        assert "digest          sha256:" in result.stdout

    def test_list_reports_the_registries_as_json(self):
        payload = json.loads(aievals("list").stdout)
        assert "contains_all" in payload["graders"]
        assert "replay" in payload["providers"]
        assert "empty" in payload["mutators"]

    def test_list_can_be_narrowed(self):
        payload = json.loads(aievals("list", "graders").stdout)
        assert set(payload) == {"graders"}


class TestRun:
    def test_the_shipped_example_passes(self):
        result = aievals("run", *EXAMPLE)
        assert result.returncode == EXIT_OK, result.stderr
        assert json.loads(result.stdout)["passed"] is True

    def test_the_report_goes_to_stdout_and_logs_to_stderr(self):
        # A log line in the middle of the report is an unparseable report.
        result = aievals("run", *EXAMPLE)
        json.loads(result.stdout)
        assert "run.complete" in result.stderr

    def test_a_missing_suite_exits_three_not_two(self):
        # Nothing was measured, so this is not a gate failure.
        result = aievals("run", "--suite", "does-not-exist.yaml")
        assert result.returncode == EXIT_ERROR
        assert "could not be opened" in result.stderr

    def test_the_error_carries_its_remedy(self):
        result = aievals("run", "--suite", "does-not-exist.yaml")
        assert "Check the path" in result.stderr

    def test_a_pass_rate_override_can_fail_a_passing_run(self):
        result = aievals("run", *EXAMPLE, "--min-pass-rate", "1.0", "--tag", "policy")
        assert result.returncode == EXIT_OK
        assert json.loads(result.stdout)["run"]["total"] == 2

    def test_reports_are_written_where_asked(self, tmp_path: Path):
        result = aievals(
            "run",
            *EXAMPLE,
            "--json-out",
            str(tmp_path / "r.json"),
            "--junit-out",
            str(tmp_path / "r.xml"),
            "--markdown-out",
            str(tmp_path / "r.md"),
        )
        assert result.returncode == EXIT_OK
        assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["passed"]
        ElementTree.fromstring((tmp_path / "r.xml").read_text(encoding="utf-8"))
        assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Evaluation gate")

    def test_a_network_provider_is_refused_without_the_flag(self, tmp_path: Path):
        suite = tmp_path / "net.yaml"
        suite.write_text(
            "name: net\nprovider:\n  name: openai_compatible\n  model: m\n"
            "  options: {base_url: 'https://example.invalid/v1'}\n"
            "cases:\n  - {id: a, prompt: p}\n",
            encoding="utf-8",
        )
        result = aievals("run", "--suite", str(suite))
        assert result.returncode == EXIT_ERROR
        assert "hermetic" in result.stderr


class TestGate:
    def test_the_shipped_example_gates_green(self):
        result = aievals("gate", *EXAMPLE, "--baseline", str(EXAMPLES / "support-baseline.json"))
        assert result.returncode == EXIT_OK, result.stderr

    def test_a_regression_exits_two(self, tmp_path: Path):
        # The whole point of the project, asserted at the process boundary.
        cassette = json.loads((EXAMPLES / "support-cassette.json").read_text(encoding="utf-8"))
        for entry in cassette["entries"]:
            if "refund" in entry["request"]["prompt"]:
                entry["completion"]["text"] = "You can request a refund within 60 days."
        broken = tmp_path / "broken.json"
        broken.write_text(json.dumps(cassette), encoding="utf-8")

        result = aievals(
            "gate",
            "--suite",
            str(EXAMPLES / "support.yaml"),
            "--cassette",
            str(broken),
            "--judge-cassette",
            str(EXAMPLES / "support-judge-cassette.json"),
            "--judge-model",
            "demo-judge",
            "--baseline",
            str(EXAMPLES / "support-baseline.json"),
        )
        assert result.returncode == EXIT_GATE_FAILED
        assert "REGRESSION" in result.stderr
        assert "refund-window" in result.stderr

    def test_a_stale_baseline_exits_two(self, tmp_path: Path):
        edited = tmp_path / "edited.yaml"
        original = (EXAMPLES / "support.yaml").read_text(encoding="utf-8")
        edited.write_text(original.replace("min_pass_rate: 1.0", "min_pass_rate: 0.5"), "utf-8")
        result = aievals(
            "gate",
            "--suite",
            str(edited),
            "--cassette",
            str(EXAMPLES / "support-cassette.json"),
            "--judge-cassette",
            str(EXAMPLES / "support-judge-cassette.json"),
            "--judge-model",
            "demo-judge",
            "--baseline",
            str(EXAMPLES / "support-baseline.json"),
        )
        assert result.returncode == EXIT_GATE_FAILED
        assert "STALE_BASELINE" in result.stderr

    def test_a_stale_baseline_can_be_downgraded_deliberately(self, tmp_path: Path):
        edited = tmp_path / "edited.yaml"
        original = (EXAMPLES / "support.yaml").read_text(encoding="utf-8")
        edited.write_text(original.replace("min_pass_rate: 1.0", "min_pass_rate: 0.5"), "utf-8")
        result = aievals(
            "gate",
            "--suite",
            str(edited),
            "--cassette",
            str(EXAMPLES / "support-cassette.json"),
            "--judge-cassette",
            str(EXAMPLES / "support-judge-cassette.json"),
            "--judge-model",
            "demo-judge",
            "--baseline",
            str(EXAMPLES / "support-baseline.json"),
            "--allow-stale-baseline",
        )
        assert result.returncode == EXIT_OK
        assert "warn  STALE_BASELINE" in result.stderr

    def test_a_missing_baseline_exits_three(self):
        result = aievals("gate", *EXAMPLE, "--baseline", "absent.json")
        assert result.returncode == EXIT_ERROR

    def test_the_meta_gate_can_run_alongside(self):
        result = aievals(
            "gate", *EXAMPLE, "--baseline", str(EXAMPLES / "support-baseline.json"), "--mutate"
        )
        assert result.returncode == EXIT_OK, result.stderr
        assert json.loads(result.stdout)["mutation"]["score"] == 1.0


class TestBaselineCommand:
    def test_it_writes_a_baseline_a_gate_then_accepts(self, tmp_path: Path):
        out = tmp_path / "b.json"
        recorded = aievals("baseline", *EXAMPLE, "--out", str(out), "--note", "first")
        assert recorded.returncode == EXIT_OK
        assert json.loads(recorded.stdout)["pass_rate"] == 1.0

        gated = aievals("gate", *EXAMPLE, "--baseline", str(out))
        assert gated.returncode == EXIT_OK


class TestMutateCommand:
    def test_the_shipped_example_catches_every_core_mutant(self):
        result = aievals("mutate", *EXAMPLE)
        assert result.returncode == EXIT_OK, result.stderr
        assert json.loads(result.stdout)["score"] == 1.0

    def test_a_weak_suite_exits_two_and_names_the_survivors(self, tmp_path: Path):
        suite = tmp_path / "weak.yaml"
        suite.write_text(
            "name: weak\nprovider: {name: scripted, options: {responses: "
            '{"p": "a reasonably long answer that says nothing checkable at all"}}}\n'
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_none, params: {values: [never appears here]}}]}\n",
            encoding="utf-8",
        )
        result = aievals("mutate", "--suite", str(suite))
        assert result.returncode == EXIT_GATE_FAILED
        assert "SURVIVED" in result.stderr
        assert "below the required" in result.stderr

    def test_the_threshold_can_be_relaxed(self, tmp_path: Path):
        suite = tmp_path / "weak.yaml"
        suite.write_text(
            "name: weak\nprovider: {name: scripted, options: {responses: "
            '{"p": "a reasonably long answer that says nothing checkable at all"}}}\n'
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_none, params: {values: [never appears here]}}]}\n",
            encoding="utf-8",
        )
        result = aievals("mutate", "--suite", str(suite), "--min-caught", "0.0")
        assert result.returncode == EXIT_OK

    def test_an_unknown_mutator_is_refused_with_the_known_ones(self):
        result = aievals("mutate", *EXAMPLE, "--mutator", "nonsense")
        assert result.returncode == EXIT_ERROR
        assert "Known:" in result.stderr

    def test_the_extended_set_can_be_selected(self):
        result = aievals("mutate", *EXAMPLE, "--set", "extended", "--min-caught", "0.0")
        assert result.returncode == EXIT_OK
        assert json.loads(result.stdout)["metadata"]["mutators"]


class TestRecordCommand:
    def test_recording_without_the_network_flag_is_refused(self):
        result = aievals("record", *EXAMPLE, "--out", "unused.json")
        assert result.returncode == EXIT_ERROR
        assert "--allow-network" in result.stderr


class TestExampleIsReproducible:
    def test_the_cassette_builder_regenerates_identical_recordings(self, tmp_path: Path):
        # Apart from timestamps: a worked example nobody can rebuild is a
        # worked example that quietly stops matching its suite.
        work = tmp_path / "repo"
        work.mkdir()
        shutil.copytree(EXAMPLES, work / "examples")
        shutil.copytree(EXAMPLES.parent / "scripts", work / "scripts")
        shutil.copytree(EXAMPLES.parent / "src", work / "src")

        result = subprocess.run(
            [sys.executable, str(work / "scripts" / "build-example-cassette.py")],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stderr

        rebuilt = json.loads((work / "examples" / "support-cassette.json").read_text("utf-8"))
        original = json.loads((EXAMPLES / "support-cassette.json").read_text("utf-8"))
        assert [entry["fingerprint"] for entry in rebuilt["entries"]] == [
            entry["fingerprint"] for entry in original["entries"]
        ]
        assert [entry["completion"]["text"] for entry in rebuilt["entries"]] == [
            entry["completion"]["text"] for entry in original["entries"]
        ]

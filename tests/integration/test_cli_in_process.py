"""The command line, driven in process.

This layer exists alongside ``tests/e2e/test_cli.py`` rather than instead of it,
because the two answer different questions. The end-to-end tests spawn a real
process and are the only thing that can show ``echo $?`` is 2 or that a cp1252
console does not crash the tool. They cannot show which branch of
``_command_gate`` ran, and nothing they do is visible to a coverage run of the
parent process — a subprocess is a different interpreter.

So the dispatch itself is tested here: every command, both sides of every
decision the command line makes on its own, and the mapping from a raised
``AievalsError`` to an exit code. Without this file, ``cli.py`` is the largest
module in the project and the least examined.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from aievals.cli import DOCTOR_SUITE, main
from aievals.errors import EXIT_ERROR, EXIT_GATE_FAILED, EXIT_OK
from aievals.providers.base import Completion, CompletionRequest
from aievals.providers.cassette import Cassette
from aievals.suite import Suite, parse_suite
from tests.conftest import EXAMPLES

pytestmark = pytest.mark.integration


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


@pytest.fixture(autouse=True)
def _neutral_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test as if on a machine with no settings of its own.

    ``Settings`` reads ``AIEVALS_*`` and a ``.env`` in the working directory.
    A developer with either one set would otherwise see different results from
    CI, which is the failure mode the settings module exists to avoid.
    """
    for name in tuple(os.environ):
        if name.startswith("AIEVALS_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def replayed_suite(tmp_path: Path, cassette_for: Any) -> Any:
    """Write a suite and a cassette, and return the arguments that replay them.

    The cassette is built from the answers the caller gives, so a test can make
    a case pass or fail by changing one string.
    """

    def build(responses: dict[str, str], *, thresholds: str = "") -> tuple[Suite, list[str]]:
        # Dedent first and substitute after. Interpolating a multi-line block
        # into the literal would give `textwrap.dedent` a line with no common
        # prefix, which silently flattens the indentation of the whole file and
        # produces a YAML error a long way from its cause.
        text = textwrap.dedent(
            """
            name: simple
            description: Two cases, no model needed.
            provider:
              name: scripted
              model: test-model
            THRESHOLDS
            cases:
              - id: alpha
                prompt: say alpha
                graders:
                  - type: contains_all
                    params:
                      values: ["alpha"]
              - id: beta
                prompt: say beta
                graders:
                  - type: contains_all
                    params:
                      values: ["beta"]
            """
        ).replace("THRESHOLDS", thresholds)
        suite_path = tmp_path / "simple.yaml"
        suite_path.write_text(text, encoding="utf-8")
        suite = parse_suite(text, origin=str(suite_path))
        cassette_path = tmp_path / "simple-cassette.json"
        cassette_for(suite, responses).save(cassette_path)
        return suite, ["--suite", str(suite_path), "--cassette", str(cassette_path)]

    return build


PASSING = {"alpha": "here is alpha", "beta": "here is beta"}
FAILING = {"alpha": "here is alpha", "beta": "nothing useful here"}


def _blind_suite(tmp_path: Path) -> tuple[Path, Path]:
    """A suite that accepts anything, and a recording it accepts.

    ``regex: .*`` matches every string including the empty one. It is the
    canonical grader that measures nothing, and a suite built from it passes
    every run while proving nothing at all.
    """
    suite_path = tmp_path / "blind.yaml"
    suite_path.write_text(
        textwrap.dedent(
            """
            name: blind
            description: A suite that accepts anything.
            provider:
              name: scripted
              model: test-model
            cases:
              - id: alpha
                prompt: say alpha
                graders:
                  - type: regex
                    params:
                      pattern: ".*"
            """
        ),
        encoding="utf-8",
    )
    suite = parse_suite(suite_path.read_text(encoding="utf-8"), origin=str(suite_path))
    cassette = Cassette()
    cassette.put(
        CompletionRequest(
            prompt=suite.cases[0].prompt,
            model=suite.provider.model,
            system=suite.cases[0].system,
            temperature=suite.provider.temperature,
            max_tokens=suite.provider.max_tokens,
        ),
        Completion(text="anything at all", model="test-model", provider="test"),
    )
    cassette_path = tmp_path / "blind-cassette.json"
    cassette.save(cassette_path)
    return suite_path, cassette_path


class TestDispatch:
    def test_run_reports_json_on_stdout_and_a_verdict_on_stderr(
        self, replayed_suite: Any, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)

        assert main(["run", *args]) == EXIT_OK

        captured = capsys.readouterr()
        # The split is load-bearing: a build script parses stdout.
        document = json.loads(captured.out)
        assert document["run"]["passed"] == 2
        assert "PASSED  simple: 2/2" in captured.err

    def test_a_suite_that_declares_no_floor_gets_no_floor(self, replayed_suite: Any):
        # Deliberate, and the sharp edge in the tool: `run` applies the suite's
        # own thresholds, and a suite that declares none has nothing to apply.
        # A half-failing suite therefore exits 0 here.
        #
        # This is why `gate` and not `run` is the CI command. Under `gate` the
        # baseline is the floor, which is the whole thesis: an existing suite
        # that has always scored 18/20 should go red when it scores 17, not
        # because it was never 20/20.
        _, args = replayed_suite(FAILING)
        assert main(["run", *args]) == EXIT_OK

    def test_a_declared_floor_is_enforced(self, replayed_suite: Any):
        _, args = replayed_suite(FAILING, thresholds="thresholds:\n  min_pass_rate: 1.0")
        assert main(["run", *args]) == EXIT_GATE_FAILED

    def test_run_honours_an_explicit_minimum_pass_rate(self, replayed_suite: Any):
        _, args = replayed_suite(FAILING)
        # 50% of the cases pass, so a 50% floor is met and a 60% floor is not.
        assert main(["run", *args, "--min-pass-rate", "0.5"]) == EXIT_OK
        assert main(["run", *args, "--min-pass-rate", "0.6"]) == EXIT_GATE_FAILED

    @pytest.mark.parametrize(
        ("what", "expected"),
        [
            ("mutators", "truncate"),
            ("graders", "contains_all"),
            ("providers", "replay"),
        ],
    )
    def test_list_narrows_to_one_registry(
        self, what: str, expected: str, capsys: pytest.CaptureFixture[str]
    ):
        assert main(["list", what]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {what}
        assert expected in payload[what]

    def test_list_reports_all_three_registries_by_default(self, capsys: pytest.CaptureFixture[str]):
        assert main(["list"]) == EXIT_OK
        assert set(json.loads(capsys.readouterr().out)) == {"graders", "providers", "mutators"}

    def test_doctor_self_checks_without_a_recording(self, capsys: pytest.CaptureFixture[str]):
        assert main(["doctor"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "self-check      ok" in out
        # The doctor suite is the proof that the pipeline works with no
        # credential and no cassette; if it stops being self-contained the
        # command stops being a self-check.
        assert parse_suite(DOCTOR_SUITE, origin="<doctor>").provider.name == "echo"

    def test_doctor_inspects_a_named_suite(
        self, replayed_suite: Any, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        assert main(["doctor", "--suite", args[1]]) == EXIT_OK
        assert "digest          sha256:" in capsys.readouterr().out


class TestBaselineAndGate:
    def _baseline(self, args: list[str], tmp_path: Path) -> Path:
        out = tmp_path / "baseline.json"
        assert (
            main(["baseline", *args, "--out", str(out), "--note", "recorded by a test"]) == EXIT_OK
        )
        return out

    def test_baseline_records_what_the_suite_does_today(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        out = self._baseline(args, tmp_path)

        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["note"] == "recorded by a test"
        assert json.loads(capsys.readouterr().out)["pass_rate"] == 1.0

    def test_gate_passes_against_its_own_baseline(self, replayed_suite: Any, tmp_path: Path):
        _, args = replayed_suite(PASSING)
        baseline = self._baseline(args, tmp_path)

        assert main(["gate", *args, "--baseline", str(baseline)]) == EXIT_OK

    def test_gate_fails_when_a_case_that_passed_now_fails(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        baseline = self._baseline(args, tmp_path)
        # Re-record the same suite with a worse answer: same cases, same
        # digest, one flip. This is the regression the whole tool is for.
        _, args = replayed_suite(FAILING)

        assert main(["gate", *args, "--baseline", str(baseline)]) == EXIT_GATE_FAILED
        assert "REGRESSION" in capsys.readouterr().err

    def test_gate_writes_every_report_format_it_is_asked_for(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        baseline = self._baseline(args, tmp_path)
        # Recording the baseline printed its own line; the assertion below is
        # about what the gate writes, not about what came before it.
        capsys.readouterr()
        json_out = tmp_path / "reports" / "report.json"
        junit_out = tmp_path / "reports" / "junit.xml"
        markdown_out = tmp_path / "reports" / "summary.md"

        assert (
            main(
                [
                    "gate",
                    *args,
                    "--baseline",
                    str(baseline),
                    "--json-out",
                    str(json_out),
                    "--junit-out",
                    str(junit_out),
                    "--markdown-out",
                    str(markdown_out),
                ]
            )
            == EXIT_OK
        )

        # A directory that did not exist is created, so a CI job need not
        # mkdir before running the gate.
        assert json.loads(json_out.read_text(encoding="utf-8"))["run"]["passed"] == 2
        assert ElementTree.fromstring(junit_out.read_text(encoding="utf-8")).tag == "testsuite"
        assert "simple" in markdown_out.read_text(encoding="utf-8")
        # With --json-out the report goes to the file and not to stdout, or a
        # job that redirects stdout gets it twice.
        assert capsys.readouterr().out == ""

    def test_responses_are_only_included_when_asked_for(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)

        main(["run", *args])
        without = capsys.readouterr().out
        main(["run", *args, "--include-responses"])
        with_responses = capsys.readouterr().out

        assert "here is alpha" not in without
        assert "here is alpha" in with_responses

    def test_a_baseline_for_a_different_suite_is_a_hard_failure(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        baseline = self._baseline(args, tmp_path)
        # A third case changes the digest, so the baseline is measuring
        # something else and comparing them would be meaningless.
        suite_path = Path(args[1])
        # Written at the indentation the file already uses rather than via
        # dedent, which strips the two spaces that make it a list item.
        third_case = textwrap.indent(
            textwrap.dedent(
                """
                - id: gamma
                  prompt: say gamma
                  graders:
                    - type: contains_all
                      params:
                        values: ["gamma"]
                """
            ).strip()
            + "\n",
            "  ",
        )
        suite_path.write_text(suite_path.read_text(encoding="utf-8") + third_case, encoding="utf-8")
        capsys.readouterr()

        # Exit 2: the build is red and a person has to act, by re-recording.
        # It is not exit 3, because nothing about the harness broke — the run
        # happened, it just cannot be compared to that baseline.
        assert main(["gate", *args, "--baseline", str(baseline)]) == EXIT_GATE_FAILED
        err = capsys.readouterr().err
        assert "STALE_BASELINE" in err
        # And it says so before anything downstream, because every other
        # finding in that report is a fact about a different suite.
        assert err.index("STALE_BASELINE") < err.index("CASE_ERRORS")


class TestMutate:
    def test_the_worked_example_catches_every_core_mutant(self, capsys: pytest.CaptureFixture[str]):
        assert main(["mutate", *EXAMPLE]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["score"] == 1.0
        assert payload["survived"] == 0

    def test_a_suite_that_cannot_discriminate_fails_the_meta_gate(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        suite_path, cassette_path = _blind_suite(tmp_path)

        code = main(["mutate", "--suite", str(suite_path), "--cassette", str(cassette_path)])

        assert code == EXIT_GATE_FAILED
        assert json.loads(capsys.readouterr().out)["survived"] > 0

    def test_gate_fails_on_a_blind_suite_even_though_nothing_regressed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        # The case the meta-gate exists for, through the CI command: the model
        # is fine, the baseline matches, every case passes — and the suite is
        # measuring nothing. `gate --mutate` is the only thing that goes red.
        suite_path, cassette_path = _blind_suite(tmp_path)
        args = ["--suite", str(suite_path), "--cassette", str(cassette_path)]
        baseline = tmp_path / "blind-baseline.json"
        assert main(["baseline", *args, "--out", str(baseline)]) == EXIT_OK
        assert main(["gate", *args, "--baseline", str(baseline)]) == EXIT_OK
        capsys.readouterr()

        code = main(["gate", *args, "--baseline", str(baseline), "--mutate"])

        assert code == EXIT_GATE_FAILED
        err = capsys.readouterr().err
        # Reported as its own failure, not folded into the gate findings: "the
        # model got worse" and "the suite cannot tell" call for different work.
        assert "MUTATION_SCORE" in err
        assert "PASSED" in err

    def test_a_lower_bar_can_be_asked_for_explicitly(self, replayed_suite: Any):
        # The extended set names real gaps rather than failing the build, so
        # --min-caught 0 has to be honoured or the diagnostic is unusable.
        assert main(["mutate", *EXAMPLE, "--set", "extended", "--min-caught", "0"]) == EXIT_OK

    def test_an_unknown_mutator_is_named(self, capsys: pytest.CaptureFixture[str]):
        assert main(["mutate", *EXAMPLE, "--mutator", "no-such-mutator"]) == EXIT_ERROR
        assert "no-such-mutator" in capsys.readouterr().err

    def test_the_report_can_be_written_to_a_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        out = tmp_path / "mutation.json"
        assert main(["mutate", *EXAMPLE, "--json-out", str(out)]) == EXIT_OK
        assert json.loads(out.read_text(encoding="utf-8"))["score"] == 1.0
        assert capsys.readouterr().out == ""


class TestHermeticEnforcement:
    def test_record_refuses_without_an_explicit_opt_in(
        self, replayed_suite: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        # Recording is the one command that reaches a network, so it is the one
        # command that has to say so out loud before it does.
        _, args = replayed_suite(PASSING)
        code = main(["record", "--suite", args[1], "--out", str(tmp_path / "out.json")])

        assert code == EXIT_ERROR
        err = capsys.readouterr().err
        assert "hermetic" in err
        assert "--allow-network" in err
        assert not (tmp_path / "out.json").exists()

    def test_record_captures_a_cassette_once_the_opt_in_is_given(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        # `echo` is a provider, so the capture loop can be exercised without a
        # vendor, a credential or a socket. What is being tested is the loop and
        # the artefact it writes, not the transport.
        suite_path = tmp_path / "echoed.yaml"
        suite_path.write_text(
            textwrap.dedent(
                """
                name: echoed
                description: Two cases through the echo provider.
                provider:
                  name: echo
                  model: echo
                cases:
                  - id: alpha
                    prompt: say alpha
                    graders:
                      - type: contains_all
                        params:
                          values: ["alpha"]
                  - id: beta
                    prompt: say beta
                    graders:
                      - type: contains_all
                        params:
                          values: ["beta"]
                """
            ),
            encoding="utf-8",
        )
        out = tmp_path / "recorded.json"

        code = main(["record", "--suite", str(suite_path), "--out", str(out), "--allow-network"])

        assert code == EXIT_OK
        assert json.loads(capsys.readouterr().out)["entries"] == 2
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert len(payload["entries"]) == 2
        # And the recording is immediately usable, which is the only thing that
        # makes `record` worth having.
        assert main(["run", "--suite", str(suite_path), "--cassette", str(out)]) == EXIT_OK

    def test_a_networked_provider_cannot_be_built_for_a_hermetic_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        suite_path = tmp_path / "live.yaml"
        suite_path.write_text(
            textwrap.dedent(
                """
                name: live
                description: Names a provider that opens a socket.
                provider:
                  name: openai_compatible
                  model: gpt-4o-mini
                  options:
                    base_url: https://example.invalid/v1
                    api_key_env: NOT_SET_ANYWHERE
                cases:
                  - id: alpha
                    prompt: say alpha
                    graders:
                      - type: contains_all
                        params:
                          values: ["alpha"]
                """
            ),
            encoding="utf-8",
        )

        assert main(["run", "--suite", str(suite_path)]) == EXIT_ERROR
        assert "hermetic" in capsys.readouterr().err


class TestErrorsAndOptions:
    def test_a_missing_suite_is_an_error_not_a_crash(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        code = main(["run", "--suite", str(tmp_path / "absent.yaml")])
        assert code == EXIT_ERROR
        # Exit 3, not 2: nothing was measured, so nothing regressed.
        assert code != EXIT_GATE_FAILED
        assert capsys.readouterr().err.strip()

    def test_a_missing_cassette_entry_is_reported_against_the_case(
        self, replayed_suite: Any, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        suite_path = Path(args[1])
        suite_path.write_text(
            suite_path.read_text(encoding="utf-8").replace("say beta", "say something else"),
            encoding="utf-8",
        )

        assert main(["run", *args]) == EXIT_GATE_FAILED
        assert "CASE_ERRORS" in capsys.readouterr().err

    def test_tags_select_a_subset_of_the_cases(self, capsys: pytest.CaptureFixture[str]):
        assert main(["run", *EXAMPLE, "--tag", "policy"]) == EXIT_OK
        document = json.loads(capsys.readouterr().out)
        assert 0 < document["run"]["total"] < 7
        assert document["run"]["passed"] == document["run"]["total"]

    def test_the_log_level_can_be_overridden_for_one_invocation(self, replayed_suite: Any):
        _, args = replayed_suite(PASSING)
        assert main(["--log-level", "DEBUG", "run", *args]) == EXIT_OK

    def test_concurrency_and_samples_come_from_the_command_line_when_given(
        self, replayed_suite: Any, capsys: pytest.CaptureFixture[str]
    ):
        _, args = replayed_suite(PASSING)
        assert main(["run", *args, "--concurrency", "1", "--samples", "3"]) == EXIT_OK
        document = json.loads(capsys.readouterr().out)
        # Three samples of each of two cases, replayed from the same recording.
        assert document["run"]["sampled"] is True
        assert [len(case["samples"]) for case in document["run"]["cases"]] == [3, 3]

    def test_settings_come_from_the_environment_when_the_flag_is_absent(
        self, replayed_suite: Any, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AIEVALS_RUN__CONCURRENCY", "2")
        _, args = replayed_suite(PASSING)
        assert main(["run", *args]) == EXIT_OK

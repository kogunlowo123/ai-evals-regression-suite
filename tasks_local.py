"""Tasks specific to this repository, merged over the ones in ``tasks.py``.

Two things are overridden rather than added. ``setup`` uses dependency groups
instead of extras, because the documentation toolchain is deliberately separate
from the development one — a Pages build should not install pytest. And ``test``
carries this project's coverage gate.

The rest are the commands this repository's own gates run, so that what CI does
and what a contributor can run locally are the same list.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IMAGE = os.environ.get("IMAGE_NAME", "ai-evals-regression-suite")

UV = "uv"

SUITE = "examples/support.yaml"
CASSETTE = "examples/support-cassette.json"
JUDGE_CASSETTE = "examples/support-judge-cassette.json"
BASELINE = "examples/support-baseline.json"

#: Line coverage required. Not 100: the last few percent are error paths that
#: need a broken filesystem or a killed process to reach, and chasing them
#: produces tests that assert the implementation rather than the behaviour.
COVERAGE_FLOOR = "90"


def _uv(*args: str) -> list[str]:
    return [UV, *args]


def _run(*args: str) -> list[str]:
    return [UV, "run", *args]


def _example(*args: str) -> list[str]:
    return _run(
        "aievals",
        *args,
        "--suite",
        SUITE,
        "--cassette",
        CASSETTE,
        "--judge-cassette",
        JUDGE_CASSETTE,
        "--judge-model",
        "demo-judge",
    )


TASKS = {
    "setup": (
        "Create the virtual environment and install every dependency group.",
        [_uv("sync", "--locked", "--group", "dev", "--group", "docs")],
    ),
    "test": (
        "Run the whole test suite with the coverage gate.",
        [
            _run(
                "pytest",
                "--cov",
                "--cov-report=term-missing",
                f"--cov-fail-under={COVERAGE_FLOOR}",
            )
        ],
    ),
    "test-e2e": (
        "Run the end-to-end tests only: the CLI as a real process.",
        [_run("pytest", "-m", "e2e")],
    ),
    "test-meta": (
        "Run the negative controls: break a requirement, assert the build goes red.",
        [_run("pytest", "-m", "meta")],
    ),
    "gate": (
        "Run the worked example through the gate, exactly as CI does.",
        [_example("gate", "--baseline", BASELINE, "--mutate")],
    ),
    "mutate": (
        "Run the meta-gate over the worked example.",
        [_example("mutate")],
    ),
    "mutate-extended": (
        "The diagnostic mutator set: what is this suite blind to?",
        [_example("mutate", "--set", "extended", "--min-caught", "0")],
    ),
    "doctor": (
        "Report what this installation would do, and self-check end to end.",
        [_run("aievals", "doctor", "--suite", SUITE)],
    ),
    "fixtures": (
        "Rebuild the example cassettes and baseline from the authored answers.",
        [
            _run("python", "scripts/build-example-cassette.py"),
            _example("baseline", "--out", BASELINE, "--note", "the authored example answers"),
        ],
    ),
    "examples": (
        "Run every example. They are documentation that executes.",
        [
            _run("python", "examples/quickstart.py"),
            _run("python", "examples/mutation_demo.py"),
            _run("python", "examples/statistics_demo.py"),
        ],
    ),
    "site": (
        "Build the documentation site into _site/.",
        [_run("--only-group", "docs", "python", "scripts/build_site.py", "--output", "_site")],
    ),
    "docker-build": (
        "Build the container image.",
        [["docker", "build", "-t", f"{IMAGE}:local", "."]],
    ),
    "smoke": (
        "Build the image and run the smoke test against it.",
        [
            ["docker", "build", "-t", f"{IMAGE}:local", "."],
            ["bash", "scripts/smoke-test.sh", f"{IMAGE}:local"],
        ],
    ),
}

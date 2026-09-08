"""Shared fixtures.

Credential-shaped strings are built by concatenation — ``"AKIA" + "IOSFO..."`` —
so a scanner reading this repository does not report a fixture as a finding, and
so a human can see at a glance that nothing here was ever valid.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.providers.cassette import Cassette
from aievals.runner import Runner, RunOptions
from aievals.suite import Suite, parse_suite

# -- inert credential-shaped fixtures ------------------------------------

FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_GITHUB_TOKEN = "ghp_" + "a" * 36
FAKE_OPENAI_KEY = "sk-" + "b" * 32
FAKE_BEARER = "Bearer " + "c" * 40

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"


SIMPLE_SUITE = textwrap.dedent(
    """
    name: simple
    description: Two cases, no model needed.
    provider:
      name: scripted
      model: test-model
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
)


@pytest.fixture
def simple_suite() -> Suite:
    """A two-case suite that needs no provider configuration."""
    return parse_suite(SIMPLE_SUITE, origin="<simple>")


@pytest.fixture
def suite_file(tmp_path: Path) -> Path:
    """The simple suite, written to a temporary file."""
    path = tmp_path / "simple.yaml"
    path.write_text(SIMPLE_SUITE, encoding="utf-8")
    return path


class RecordingProvider(Provider):
    """A provider under the test's control, with a call log."""

    name = "recording"
    reaches_network = False

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        super().__init__({})
        self.answers = answers or {}
        self.requests: list[CompletionRequest] = []
        self.default = ""

    async def complete(self, request: CompletionRequest) -> Completion:
        """Return the configured answer for the prompt."""
        self.requests.append(request)
        return Completion(
            text=self.answers.get(request.prompt, self.default),
            model=request.model,
            provider=self.name,
            latency_ms=1.0,
            prompt_tokens=7,
            completion_tokens=11,
        )


@pytest.fixture
def answers() -> dict[str, str]:
    """Responses that make the simple suite pass."""
    return {"say alpha": "here is alpha for you", "say beta": "here is beta for you"}


@pytest.fixture
def provider(answers: dict[str, str]) -> RecordingProvider:
    """A provider answering the simple suite correctly."""
    return RecordingProvider(answers)


@pytest.fixture
def run_suite() -> Any:
    """A helper that runs a suite against a provider and returns the result."""

    async def run(
        suite: Suite,
        used: Provider,
        *,
        options: RunOptions | None = None,
        judge: Provider | None = None,
    ):
        runner = Runner(used, options=options, judge=judge, judge_model="judge-model")
        return await runner.run(suite)

    return run


@pytest.fixture
def cassette_for() -> Any:
    """Build a cassette from a mapping of prompt to response."""

    def build(suite: Suite, responses: dict[str, str], *, provider_name: str = "test") -> Cassette:
        cassette = Cassette()
        for case in suite.cases:
            request = CompletionRequest(
                prompt=case.prompt,
                model=suite.provider.model,
                system=case.system,
                temperature=suite.provider.temperature,
                max_tokens=suite.provider.max_tokens,
            )
            cassette.put(
                request,
                Completion(
                    text=responses.get(case.id, ""),
                    model=suite.provider.model,
                    provider=provider_name,
                    latency_ms=5.0,
                    prompt_tokens=3,
                    completion_tokens=5,
                ),
            )
        return cassette

    return build


@pytest.fixture
def example_suite_path() -> Path:
    """The worked example shipped in the repository."""
    return EXAMPLES / "support.yaml"


@pytest.fixture
def example_cassette_path() -> Path:
    """Its cassette."""
    return EXAMPLES / "support-cassette.json"


@pytest.fixture
def example_judge_cassette_path() -> Path:
    """Its judge cassette."""
    return EXAMPLES / "support-judge-cassette.json"


@pytest.fixture
def example_baseline_path() -> Path:
    """Its baseline."""
    return EXAMPLES / "support-baseline.json"

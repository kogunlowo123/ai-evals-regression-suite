"""The runner, wired to real providers and real graders."""

from __future__ import annotations

import asyncio

import pytest

from aievals.errors import AievalsError, ProviderError
from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.runner import RunOptions
from aievals.suite import parse_suite
from tests.conftest import RecordingProvider

pytestmark = pytest.mark.integration

TAGGED = """
name: tagged
provider:
  name: scripted
cases:
  - id: one
    prompt: p1
    tags: [fast]
    graders: [{type: contains_all, params: {values: ["ok"]}}]
  - id: two
    prompt: p2
    tags: [slow]
    graders: [{type: contains_all, params: {values: ["ok"]}}]
  - id: three
    prompt: p3
    tags: [fast, slow]
    graders: [{type: contains_all, params: {values: ["ok"]}}]
"""


class SlowProvider(Provider):
    name = "slow"
    reaches_network = False

    def __init__(self, delay: float) -> None:
        super().__init__({})
        self.delay = delay
        self.concurrent = 0
        self.peak = 0

    async def complete(self, request: CompletionRequest) -> Completion:
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.concurrent -= 1
        return Completion(text="ok", model=request.model, provider=self.name)


class RaisingProvider(Provider):
    name = "raising"
    reaches_network = False

    async def complete(self, request: CompletionRequest) -> Completion:
        raise ProviderError("the model is on fire")


class TestBasicRun:
    async def test_a_passing_suite_reports_every_case_passing(
        self, simple_suite, provider, run_suite
    ):
        result = await run_suite(simple_suite, provider)
        assert result.total == 2
        assert result.pass_rate == 1.0

    async def test_a_failing_response_fails_its_case(self, simple_suite, run_suite):
        result = await run_suite(simple_suite, RecordingProvider({"say alpha": "nothing here"}))
        assert result.case("alpha").passed is False
        assert result.case("beta").passed is False

    async def test_the_result_carries_the_suite_digest(self, simple_suite, provider, run_suite):
        result = await run_suite(simple_suite, provider)
        assert result.suite_digest.startswith("sha256:")

    async def test_the_system_message_reaches_the_provider(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, system: be brief}\n"
        )
        used = RecordingProvider()
        await run_suite(suite, used)
        assert used.requests[0].system == "be brief"

    async def test_the_sampling_parameters_reach_the_provider(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted, model: m, temperature: 0.7, max_tokens: 64}\n"
            "cases:\n  - {id: a, prompt: p}\n"
        )
        used = RecordingProvider()
        await run_suite(suite, used)
        assert (used.requests[0].temperature, used.requests[0].max_tokens) == (0.7, 64)


class TestOrdering:
    async def test_results_follow_the_suite_not_the_completion_order(self, run_suite):
        suite = parse_suite(TAGGED)

        class Reversed(Provider):
            name = "reversed"
            reaches_network = False

            async def complete(self, request: CompletionRequest) -> Completion:
                # The last case answers first.
                await asyncio.sleep(0.03 if request.prompt == "p1" else 0.0)
                return Completion(text="ok", model=request.model, provider=self.name)

        result = await run_suite(suite, Reversed())
        assert [case.case_id for case in result.cases] == ["one", "two", "three"]


class TestConcurrency:
    async def test_the_limit_is_respected(self, run_suite):
        suite = parse_suite(TAGGED)
        used = SlowProvider(0.02)
        await run_suite(suite, used, options=RunOptions(concurrency=2))
        assert used.peak <= 2

    async def test_a_concurrency_below_one_is_refused(self):
        with pytest.raises(ValueError, match="concurrency"):
            RunOptions(concurrency=0)

    async def test_a_sample_count_above_the_cap_is_refused(self):
        with pytest.raises(ValueError, match="samples"):
            RunOptions(samples=101)


class TestFailureHandling:
    async def test_a_provider_error_becomes_a_failed_case_not_a_failed_run(
        self, simple_suite, run_suite
    ):
        result = await run_suite(simple_suite, RaisingProvider())
        assert result.total == 2
        assert result.errored_cases == ("alpha", "beta")
        assert "on fire" in result.case("alpha").error

    async def test_a_timeout_becomes_a_failed_case(self, simple_suite, run_suite):
        result = await run_suite(
            simple_suite, SlowProvider(1.0), options=RunOptions(case_timeout_s=0.02)
        )
        assert "no completion within" in result.case("alpha").error

    async def test_a_grader_that_raises_fails_the_case_rather_than_being_skipped(
        self, run_suite, monkeypatch
    ):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - id: a\n    prompt: p\n    graders:\n"
            "      - {type: contains_all, params: {values: [ok]}}\n"
        )

        async def explode(self, response, case, context):
            raise RuntimeError("grader broke")

        from aievals.graders.text import ContainsAll

        monkeypatch.setattr(ContainsAll, "grade", explode)
        result = await run_suite(suite, RecordingProvider({"p": "ok"}))
        assert not result.case("a").passed
        assert "grader broke" in result.case("a").samples[0].grades[0].detail

    async def test_cancellation_is_not_recorded_as_a_case_failure(self, simple_suite, run_suite):
        class Cancelling(Provider):
            name = "cancelling"
            reaches_network = False

            async def complete(self, request: CompletionRequest) -> Completion:
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await run_suite(simple_suite, Cancelling())


class TestSampling:
    async def test_several_samples_are_drawn(self, simple_suite, provider, run_suite):
        result = await run_suite(simple_suite, provider, options=RunOptions(samples=3))
        assert result.case("alpha").sample_count == 3
        assert result.sampled is True

    async def test_a_single_sample_is_not_marked_sampled(self, simple_suite, provider, run_suite):
        result = await run_suite(simple_suite, provider)
        assert result.sampled is False

    async def test_disagreeing_samples_are_flagged_as_flaky(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - id: a\n    prompt: p\n    graders:\n"
            "      - {type: contains_all, params: {values: [ok]}}\n"
        )

        class Alternating(Provider):
            name = "alternating"
            reaches_network = False

            def __init__(self) -> None:
                super().__init__({})
                self.calls = 0

            async def complete(self, request: CompletionRequest) -> Completion:
                self.calls += 1
                text = "ok" if self.calls % 2 else "no"
                return Completion(text=text, model=request.model, provider=self.name)

        result = await run_suite(suite, Alternating(), options=RunOptions(samples=4, policy="any"))
        assert result.flaky_cases == ("a",)


class TestTagSelection:
    async def test_only_tagged_cases_run(self, run_suite):
        suite = parse_suite(TAGGED)
        result = await run_suite(suite, RecordingProvider(), options=RunOptions(tags=("fast",)))
        assert {case.case_id for case in result.cases} == {"one", "three"}

    async def test_a_tag_matching_nothing_is_an_error_not_an_empty_pass(self, run_suite):
        # An empty run passes every gate trivially.
        suite = parse_suite(TAGGED)
        with pytest.raises(AievalsError, match="no case"):
            await run_suite(suite, RecordingProvider(), options=RunOptions(tags=("absent",)))


class TestGraderComposition:
    async def test_default_graders_apply_to_every_case(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "default_graders:\n"
            "  - {type: contains_none, params: {values: [forbidden]}}\n"
            "cases:\n  - {id: a, prompt: p}\n"
        )
        result = await run_suite(suite, RecordingProvider({"p": "this is forbidden"}))
        assert not result.case("a").passed

    async def test_a_describe_is_prefixed_onto_the_detail(self, run_suite):
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - id: a\n    prompt: p\n    graders:\n"
            "      - type: contains_all\n        describe: the policy figure\n"
            "        params: {values: [absent]}\n"
        )
        result = await run_suite(suite, RecordingProvider({"p": "nothing"}))
        assert result.case("a").samples[0].grades[0].detail.startswith("the policy figure:")

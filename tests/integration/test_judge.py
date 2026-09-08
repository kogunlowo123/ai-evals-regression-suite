"""The model-graded grader, driven by a replayed judge.

The point of these tests is that they exist at all. A judge grader that CI
cannot run is a check nobody has ever seen work; here the judge is a provider,
so it replays, so every path below runs in an ordinary test job with no
credential and no egress.
"""

from __future__ import annotations

import pytest

from aievals.errors import GraderError
from aievals.graders.base import GradeContext
from aievals.graders.judge import JUDGE_SYSTEM, Judge
from aievals.graders.registry import build_grader
from aievals.providers.base import Completion, CompletionRequest, Provider
from aievals.suite.models import Case, GraderSpec

pytestmark = pytest.mark.integration

CASE = Case(id="c", prompt="Why was I charged twice?")
CRITERION = "The response apologises and states a concrete next step."


class FixedJudge(Provider):
    """A judge that answers with whatever the test gives it."""

    name = "fixed-judge"
    reaches_network = False

    def __init__(self, answer: str) -> None:
        super().__init__({})
        self.answer = answer
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> Completion:
        self.requests.append(request)
        return Completion(text=self.answer, model=request.model, provider=self.name)


def judge_grader(**params) -> Judge:
    grader = build_grader(GraderSpec(type="judge", params={"criterion": CRITERION, **params}))
    assert isinstance(grader, Judge)
    return grader


async def grade(answer: str, response: str = "I am sorry. I have issued a refund."):
    judge = FixedJudge(answer)
    result = await judge_grader().grade(response, CASE, GradeContext(judge=judge, judge_model="j"))
    return result, judge


class TestVerdicts:
    async def test_a_pass_verdict_passes_the_case(self):
        result, _ = await grade('{"verdict": "pass", "reason": "it does both"}')
        assert result.passed
        assert "it does both" in result.detail

    async def test_a_fail_verdict_fails_the_case(self):
        result, _ = await grade('{"verdict": "fail", "reason": "no next step"}')
        assert not result.passed
        assert "no next step" in result.detail

    async def test_a_fenced_verdict_is_read(self):
        result, _ = await grade('```json\n{"verdict": "pass", "reason": "fine"}\n```')
        assert result.passed

    async def test_a_verdict_with_no_reason_still_decides(self):
        result, _ = await grade('{"verdict": "pass"}')
        assert result.passed
        assert "no reason given" in result.detail


class TestRefusalToGuess:
    async def test_prose_instead_of_json_fails_the_case(self):
        # A judge that cannot be understood has not endorsed anything, and
        # treating silence as approval is how a suite goes green while
        # measuring nothing.
        result, _ = await grade("Yes, that looks correct to me.")
        assert not result.passed
        assert "did not answer with JSON" in result.detail

    async def test_a_json_array_is_not_a_verdict(self):
        result, _ = await grade('["pass"]')
        assert not result.passed
        assert "not a JSON object" in result.detail

    async def test_an_unrecognised_verdict_fails_the_case(self):
        result, _ = await grade('{"verdict": "maybe", "reason": "unsure"}')
        assert not result.passed
        assert "'maybe'" in result.detail

    async def test_a_long_unparseable_answer_is_clipped_in_the_message(self):
        result, _ = await grade("prose " * 200)
        assert len(result.detail) < 300

    async def test_a_missing_judge_provider_is_loud_not_lenient(self):
        with pytest.raises(GraderError, match="no judge provider"):
            await judge_grader().grade("anything", CASE, GradeContext())

    async def test_the_error_says_how_to_configure_one(self):
        with pytest.raises(GraderError) as caught:
            await judge_grader().grade("anything", CASE, GradeContext())
        assert "--judge-cassette" in caught.value.remedy


class TestPromptConstruction:
    async def test_the_criterion_and_the_response_both_reach_the_judge(self):
        _, judge = await grade('{"verdict": "pass", "reason": "x"}')
        prompt = judge.requests[0].prompt
        assert CRITERION in prompt
        assert "I have issued a refund" in prompt

    async def test_the_response_under_judgement_is_fenced(self):
        # Marked, not rewritten: the judge sees the response byte for byte, and
        # the fence is what stops it from reading as a new instruction.
        _, judge = await grade('{"verdict": "pass", "reason": "x"}')
        assert "============" in judge.requests[0].prompt

    async def test_the_system_message_tells_the_judge_the_content_is_untrusted(self):
        _, judge = await grade('{"verdict": "pass", "reason": "x"}')
        assert judge.requests[0].system == JUDGE_SYSTEM
        assert "untrusted data" in JUDGE_SYSTEM

    async def test_the_prompt_is_included_by_default_and_can_be_dropped(self):
        judge = FixedJudge('{"verdict": "pass", "reason": "x"}')
        context = GradeContext(judge=judge, judge_model="j")
        await judge_grader().grade("answer", CASE, context)
        assert CASE.prompt in judge.requests[0].prompt

        without = FixedJudge('{"verdict": "pass", "reason": "x"}')
        await judge_grader(include_prompt=False).grade(
            "answer", CASE, GradeContext(judge=without, judge_model="j")
        )
        assert CASE.prompt not in without.requests[0].prompt

    async def test_the_judge_is_asked_at_temperature_zero(self):
        _, judge = await grade('{"verdict": "pass", "reason": "x"}')
        assert judge.requests[0].temperature == 0.0


class TestConfiguration:
    def test_a_missing_criterion_is_refused(self):
        with pytest.raises(GraderError, match="criterion"):
            build_grader(GraderSpec(type="judge", params={}))

    def test_an_empty_criterion_is_refused(self):
        with pytest.raises(GraderError, match="non-empty"):
            build_grader(GraderSpec(type="judge", params={"criterion": "   "}))

    def test_an_enormous_criterion_is_refused(self):
        with pytest.raises(GraderError, match="one requirement"):
            build_grader(GraderSpec(type="judge", params={"criterion": "x" * 2001}))

    def test_a_non_boolean_include_prompt_is_refused(self):
        with pytest.raises(GraderError, match="true or false"):
            build_grader(
                GraderSpec(type="judge", params={"criterion": "c", "include_prompt": "yes"})
            )

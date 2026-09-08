"""Executing a suite.

The runner's job is narrow: request completions, grade them, and record what
happened. It does not decide whether the result is acceptable — that is
:mod:`aievals.gate`, and keeping the two apart is what lets the same run be
compared against a baseline, rendered as a report, or fed to the mutation
harness without any of them re-running it.

Three properties the implementation is arranged around:

**Results are ordered by the suite, not by completion.** Cases run
concurrently, and a report whose row order depends on which request finished
first produces a diff on every run.

**A case that raises becomes a failed case, not a failed run.** One unreachable
model or one grader blowing up should not throw away nineteen other verdicts.
The exception is recorded on the case and shows up in ``errored_cases``, which
the gate treats as a failure — an error is not a pass.

**Cancellation is not an error.** ``asyncio.CancelledError`` is re-raised rather
than swallowed into a case result, because a run that reports "20 cases failed"
after someone pressed Ctrl-C is lying about what it measured.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from aievals.errors import AievalsError
from aievals.graders.base import Grade, GradeContext
from aievals.graders.registry import build_grader
from aievals.logging import get_logger
from aievals.providers.base import CompletionRequest, Provider
from aievals.runner.result import CaseResult, RunResult, SamplePolicy, SampleResult
from aievals.suite.digest import suite_digest
from aievals.suite.models import Case, Suite

logger = get_logger(__name__)

DEFAULT_CONCURRENCY = 4
DEFAULT_CASE_TIMEOUT_S = 120.0
MAX_SAMPLES = 100


@dataclass(frozen=True, slots=True)
class RunOptions:
    """How to run, as opposed to what to run."""

    samples: int = 1
    policy: SamplePolicy = "all"
    concurrency: int = DEFAULT_CONCURRENCY
    case_timeout_s: float = DEFAULT_CASE_TIMEOUT_S
    #: Only run cases carrying at least one of these tags. Empty means all.
    tags: tuple[str, ...] = ()
    hermetic: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.samples <= MAX_SAMPLES:
            raise ValueError(f"samples must be between 1 and {MAX_SAMPLES}")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.case_timeout_s <= 0:
            raise ValueError("case_timeout_s must be positive")


class Runner:
    """Runs a suite against a provider."""

    def __init__(
        self,
        provider: Provider,
        *,
        options: RunOptions | None = None,
        judge: Provider | None = None,
        judge_model: str = "",
    ) -> None:
        self.provider = provider
        self.options = options or RunOptions()
        self.judge = judge
        self.judge_model = judge_model

    async def run(self, suite: Suite) -> RunResult:
        """Run every selected case in *suite* and return the result."""
        selected = _select(suite, self.options.tags)
        if not selected:
            raise AievalsError(
                f"no case in {suite.name!r} carries any of the tags "
                f"{', '.join(self.options.tags)}.",
                remedy="Drop --tag, or check the spelling against the suite file.",
            )

        context = GradeContext(judge=self.judge, judge_model=self.judge_model)
        limit = asyncio.Semaphore(self.options.concurrency)
        started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()

        async def one(case: Case) -> CaseResult:
            async with limit:
                return await self._run_case(suite, case, context)

        # gather preserves argument order regardless of completion order, which
        # is the ordering property this module promises.
        results = await asyncio.gather(*(one(case) for case in selected))

        duration = (time.perf_counter() - started) * 1000
        result = RunResult(
            suite_name=suite.name,
            suite_digest=suite_digest(suite),
            provider=self.provider.name,
            model=suite.provider.model,
            cases=tuple(results),
            policy=self.options.policy,
            sampled=self.options.samples > 1,
            hermetic=self.options.hermetic,
            started_at=started_at,
            duration_ms=duration,
        )
        logger.info(
            "run.complete",
            suite=suite.name,
            total=result.total,
            passed=result.passed,
            pass_rate=round(result.pass_rate, 4),
            duration_ms=round(duration, 1),
        )
        return result

    async def _run_case(self, suite: Suite, case: Case, context: GradeContext) -> CaseResult:
        samples: list[SampleResult] = []
        for _ in range(self.options.samples):
            # Sequential, not gathered: several samples of one case are draws
            # from the same distribution, and issuing them at once multiplies
            # the load a provider sees from a single case.
            samples.append(await self._run_sample(suite, case, context))  # noqa: PERF401
        return CaseResult(
            case_id=case.id,
            samples=tuple(samples),
            policy=self.options.policy,
            tags=case.tags,
        )

    async def _run_sample(self, suite: Suite, case: Case, context: GradeContext) -> SampleResult:
        request = CompletionRequest(
            prompt=case.prompt,
            model=suite.provider.model,
            system=case.system,
            temperature=suite.provider.temperature,
            max_tokens=suite.provider.max_tokens,
        )
        started = time.perf_counter()
        try:
            completion = await asyncio.wait_for(
                self.provider.complete(request), timeout=self.options.case_timeout_s
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return SampleResult(
                response="",
                grades=(),
                latency_ms=(time.perf_counter() - started) * 1000,
                error=f"no completion within {self.options.case_timeout_s}s",
            )
        except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
            return SampleResult(
                response="",
                grades=(),
                latency_ms=(time.perf_counter() - started) * 1000,
                error=_describe(exc),
            )

        grades = await self._grade(suite, case, completion.text, context)
        return SampleResult(
            response=completion.text,
            grades=grades,
            latency_ms=completion.latency_ms or (time.perf_counter() - started) * 1000,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )

    async def _grade(
        self, suite: Suite, case: Case, response: str, context: GradeContext
    ) -> tuple[Grade, ...]:
        grades: list[Grade] = []
        for spec in suite.graders_for(case):
            instance = build_grader(spec)
            try:
                grade = await instance.grade(response, case, context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken grader is a failure
                # Not a skip. A grader that raises has not endorsed the
                # response, and recording it as anything but a failure is how a
                # suite goes green while one of its checks is broken.
                grades.append(
                    Grade(
                        grader=spec.type,
                        passed=False,
                        score=0.0,
                        detail=f"the grader raised: {_describe(exc)}",
                        required=spec.required,
                    )
                )
                continue
            if spec.describe:
                grade = Grade(
                    grader=grade.grader,
                    passed=grade.passed,
                    score=grade.score,
                    detail=f"{spec.describe}: {grade.detail}" if grade.detail else spec.describe,
                    required=grade.required,
                )
            grades.append(grade)
        return tuple(grades)


def _select(suite: Suite, tags: tuple[str, ...]) -> tuple[Case, ...]:
    if not tags:
        return suite.cases
    wanted = set(tags)
    return tuple(case for case in suite.cases if wanted & set(case.tags))


def _describe(exc: BaseException) -> str:
    if isinstance(exc, AievalsError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"

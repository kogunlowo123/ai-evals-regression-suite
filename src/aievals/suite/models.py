"""The suite data model.

A suite file is **untrusted input**. It arrives from a repository, a pull
request, or a colleague, and it is data — so the model below is the boundary
that keeps it data.

The single most important thing in this module is what a :class:`GraderSpec`
*is not*. It names a grader by a short identifier resolved against a registry.
It does not carry an import path, a dotted callable, or an expression. The
obvious convenient design is::

    graders:
      - callable: mypackage.checks:looks_right   # arbitrary code execution

which turns every suite file into a program that runs with the privileges of
whoever ran the harness — in CI, that is a runner with repository credentials.
The registry indirection is the whole defence, and it is why adding a grader
means editing Python rather than editing YAML.

Every model sets ``extra="forbid"``. A mistyped key in a suite file is a silent
loss of an expectation, which is the failure mode where a gate keeps passing
because it stopped checking anything.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Case identifiers appear in baselines, reports and JUnit output, are compared
#: across runs, and end up in file names. Keep them boring.
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Suite names appear in report headers and artefact names.
SUITE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: An upper bound on suite size. Not a performance limit — a guard against a
#: generated file that would take an afternoon to run and a fortune to serve.
MAX_CASES = 10_000


class Frozen(BaseModel):
    """Base for every suite model: immutable, and strict about unknown keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GraderSpec(Frozen):
    """One expectation, naming a grader in the registry and its parameters.

    ``type`` is looked up in :mod:`aievals.graders`; anything unregistered is an
    error at load time rather than a surprise halfway through a run.
    """

    type: str = Field(min_length=1, max_length=64)
    #: Grader-specific parameters, validated by the grader itself at load time.
    params: dict[str, Any] = Field(default_factory=dict)
    #: Contribution to the case verdict. A case passes when every grader with
    #: ``required=True`` passes; optional graders are reported but not decisive,
    #: which is how a new expectation is introduced without breaking the gate.
    required: bool = True
    #: Free text shown in reports when this grader fails.
    describe: str = ""

    @field_validator("type")
    @classmethod
    def _known_shape(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError(
                f"{value!r} is not a grader name. Names are lowercase with "
                f"underscores, like 'contains_all'."
            )
        return value


class Case(Frozen):
    """One evaluation case: an input, and what a correct answer looks like."""

    id: str
    prompt: str = Field(min_length=1)
    system: str | None = None
    graders: tuple[GraderSpec, ...] = ()
    tags: tuple[str, ...] = ()
    #: Recorded in reports so a failing case links to the requirement it exists
    #: for. Evaluation suites rot when nobody remembers why a case is there.
    rationale: str = ""

    @field_validator("id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not CASE_ID_PATTERN.match(value):
            raise ValueError(
                f"{value!r} is not a usable case id. Use letters, digits, dot, "
                f"dash and underscore, up to 128 characters."
            )
        return value

    @field_validator("tags")
    @classmethod
    def _tag_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for tag in value:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", tag):
                raise ValueError(f"{tag!r} is not a usable tag.")
        return value


class Thresholds(Frozen):
    """The bounds a run must stay inside to pass the gate.

    All are optional, and an absent bound is not checked. A default that
    silently applies a limit nobody chose produces failures whose cause is a
    number in someone else's source file.
    """

    #: The fraction of cases that must pass, in ``[0, 1]``.
    min_pass_rate: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    #: The 95th percentile of per-case latency, in milliseconds.
    max_p95_latency_ms: Annotated[float, Field(gt=0)] | None = None
    #: Total tokens across the run, provider permitting.
    max_total_tokens: Annotated[int, Field(gt=0)] | None = None
    #: Significance level for the paired test under sampling. Only consulted
    #: when a run draws more than one sample per case.
    alpha: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05


class ProviderSpec(Frozen):
    """How to obtain completions for this suite.

    ``name`` selects a provider from the registry. ``options`` is passed through
    to it and validated there — the suite model deliberately does not know what
    an OpenAI-compatible endpoint needs, so that adding a provider does not mean
    editing this file.
    """

    name: str = Field(min_length=1, max_length=64)
    model: str = Field(default="", max_length=200)
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    max_tokens: Annotated[int, Field(gt=0, le=32_000)] = 1024
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
            raise ValueError(f"{value!r} is not a provider name.")
        return value


class Suite(Frozen):
    """A named collection of cases with shared defaults and gates."""

    schema_version: Literal[1] = 1
    name: str
    description: str = ""
    provider: ProviderSpec = ProviderSpec(name="replay")
    thresholds: Thresholds = Thresholds()
    #: Graders applied to every case in addition to its own. The usual content
    #: is a house rule — no refusal boilerplate, no leaked system prompt — that
    #: would otherwise be copied into every case and drift.
    default_graders: tuple[GraderSpec, ...] = ()
    cases: tuple[Case, ...]

    @field_validator("name")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not SUITE_NAME_PATTERN.match(value):
            raise ValueError(f"{value!r} is not a usable suite name.")
        return value

    @model_validator(mode="after")
    def _cases_are_usable(self) -> Suite:
        if not self.cases:
            raise ValueError(
                "a suite with no cases passes every gate trivially, which is "
                "worse than having no suite at all."
            )
        if len(self.cases) > MAX_CASES:
            raise ValueError(f"a suite may hold at most {MAX_CASES} cases.")
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(
                    f"case id {case.id!r} appears more than once. Ids key the "
                    f"baseline comparison, so a duplicate silently discards a case."
                )
            seen.add(case.id)
        return self

    def graders_for(self, case: Case) -> tuple[GraderSpec, ...]:
        """Return the graders that apply to *case*, defaults first."""
        return (*self.default_graders, *case.graders)

    def case_by_id(self, case_id: str) -> Case | None:
        """Return the case with *case_id*, or ``None``."""
        for case in self.cases:
            if case.id == case_id:
                return case
        return None

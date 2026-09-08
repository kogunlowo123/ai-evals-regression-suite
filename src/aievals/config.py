"""Environment-driven defaults.

Small on purpose. Almost everything about a run belongs in the suite file, which
is committed and reviewed; what lives here is the handful of settings that
differ between a laptop and a CI runner — concurrency, timeouts, log format —
and none of them change what a gate decides.

``extra="forbid"`` on every section, so ``AIEVALS_RUN__CONCURENCY`` is an error
rather than a silently ignored variable that leaves a CI job running at the
default and nobody the wiser. That covers a misspelt *field*; a misspelt
*section* never reaches validation at all, so :func:`load` checks for one
itself before constructing the settings.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aievals.errors import ConfigError
from aievals.runner.run import DEFAULT_CASE_TIMEOUT_S, DEFAULT_CONCURRENCY


class Section(BaseModel):
    """Base for the settings sections."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RunSettings(Section):
    """How a run executes."""

    concurrency: Annotated[int, Field(ge=1, le=64)] = DEFAULT_CONCURRENCY
    case_timeout_s: Annotated[float, Field(gt=0, le=3600)] = DEFAULT_CASE_TIMEOUT_S
    samples: Annotated[int, Field(ge=1, le=100)] = 1


class LogSettings(Section):
    """Where diagnostics go and what they look like.

    Always stderr — that is not configurable, because stdout carries the
    report.
    """

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class Settings(BaseSettings):
    """The whole configuration.

    Environment variables are ``AIEVALS_<SECTION>__<FIELD>``, for example
    ``AIEVALS_RUN__CONCURRENCY=8``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AIEVALS_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    run: RunSettings = RunSettings()
    log: LogSettings = LogSettings()


def _reject_unknown_sections(names: Iterable[str]) -> None:
    """Fail on an ``AIEVALS_`` variable whose *section* is not a real one.

    ``extra="forbid"`` catches a misspelt field — ``AIEVALS_RUN__CONCURENCY``
    raises — but not a misspelt section: pydantic-settings never builds a
    ``logs`` key to reject, so ``AIEVALS_LOGS__LEVEL=DEBUG`` is dropped before
    validation sees it. That is the same hazard in a different place, and the
    worse one, because it looks like it worked.

    This covers the process environment, which is where a CI runner sets things
    and where a mistake is invisible. A misspelt section in a ``.env`` file is
    not covered, and does not need to be: that file is in the repository, next
    to the person editing it.
    """
    prefix = "AIEVALS_"
    known = set(Settings.model_fields)
    unknown = sorted(
        name
        for name in names
        if name.startswith(prefix) and name[len(prefix) :].split("__", 1)[0].lower() not in known
    )
    if unknown:
        raise ConfigError(
            f"unknown setting(s): {', '.join(unknown)}.",
            remedy=(
                f"Sections are {', '.join(sorted(known))}. "
                f"Variables are {prefix}<SECTION>__<FIELD>, for example "
                f"{prefix}RUN__CONCURRENCY=8."
            ),
        )


def load(environ: Mapping[str, str] | None = None) -> Settings:
    """Read settings from the environment and ``.env``.

    *environ* exists so a test can supply one; production passes nothing and
    gets ``os.environ``.
    """
    _reject_unknown_sections(os.environ if environ is None else environ)
    return Settings()

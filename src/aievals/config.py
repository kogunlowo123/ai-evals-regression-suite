"""Environment-driven defaults.

Small on purpose. Almost everything about a run belongs in the suite file, which
is committed and reviewed; what lives here is the handful of settings that
differ between a laptop and a CI runner — concurrency, timeouts, log format —
and none of them change what a gate decides.

``extra="forbid"`` on every section, so ``AIEVALS_RUN__CONCURENCY`` is an error
rather than a silently ignored variable that leaves a CI job running at the
default and nobody the wiser.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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


def load() -> Settings:
    """Read settings from the environment and ``.env``."""
    return Settings()

"""Settings.

Small module, but it decides how a run behaves on a CI runner, and the reason
it forbids unknown keys is that the alternative — silently ignoring a typo —
leaves a job running at defaults with nobody the wiser. That behaviour is only
a behaviour if something asserts it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from aievals.config import LogSettings, RunSettings, Settings, load
from aievals.errors import ConfigError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _neutral_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No inherited AIEVALS_* variables, and no `.env` in reach."""
    for name in tuple(os.environ):
        if name.startswith("AIEVALS_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


class TestDefaults:
    def test_a_machine_with_no_configuration_gets_working_defaults(self):
        settings = load()
        assert settings.run.concurrency >= 1
        assert settings.run.samples == 1
        assert settings.log.format == "json"

    def test_settings_are_frozen(self):
        settings = load()
        with pytest.raises(ValidationError):
            settings.run.concurrency = 4


class TestEnvironment:
    def test_a_nested_variable_reaches_its_section(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AIEVALS_RUN__CONCURRENCY", "12")
        monkeypatch.setenv("AIEVALS_LOG__FORMAT", "console")

        settings = load()

        assert settings.run.concurrency == 12
        assert settings.log.format == "console"

    def test_a_dot_env_file_is_read(self, tmp_path: Path):
        (tmp_path / ".env").write_text("AIEVALS_RUN__SAMPLES=5\n", encoding="utf-8")
        assert load().run.samples == 5

    def test_a_real_variable_beats_the_dot_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        (tmp_path / ".env").write_text("AIEVALS_RUN__SAMPLES=5\n", encoding="utf-8")
        monkeypatch.setenv("AIEVALS_RUN__SAMPLES", "9")
        assert load().run.samples == 9

    def test_a_misspelt_variable_is_an_error_rather_than_a_shrug(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The whole reason for extra="forbid". Without it this typo leaves a CI
        # job running at the default concurrency and reporting nothing.
        monkeypatch.setenv("AIEVALS_RUN__CONCURENCY", "8")
        # Reported as a ConfigError rather than pydantic's ValidationError,
        # because the command line renders this tool's errors and lets anything
        # else escape as a traceback and exit 1 — see the e2e assertion.
        with pytest.raises(ConfigError, match="concurency"):
            load()

    def test_an_unknown_section_is_an_error_too(self, monkeypatch: pytest.MonkeyPatch):
        # A misspelt *section* never reaches pydantic — there is no `logs` key
        # for extra="forbid" to reject — so `load` checks for one itself. This
        # is the worse of the two typos: it looks like it worked.
        monkeypatch.setenv("AIEVALS_LOGS__LEVEL", "DEBUG")
        with pytest.raises(ConfigError, match="AIEVALS_LOGS__LEVEL"):
            load()

    def test_the_error_names_the_sections_that_do_exist(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AIEVALS_NETWORK__PROXY", "http://example.invalid")
        with pytest.raises(ConfigError) as caught:
            load()
        assert "run" in caught.value.remedy
        assert "log" in caught.value.remedy

    def test_variables_belonging_to_other_tools_are_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The guard is scoped to this tool's prefix. A machine has hundreds of
        # environment variables and none of the rest are its business.
        monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        monkeypatch.setenv("AIEVALSOMETHING", "unprefixed")
        assert load().run.samples == 1

    def test_an_explicit_environment_can_be_supplied(self):
        with pytest.raises(ConfigError):
            load({"AIEVALS_TYPO__FIELD": "1"})


class TestBounds:
    @pytest.mark.parametrize("value", [0, -1, 65])
    def test_concurrency_is_bounded(self, value: int):
        with pytest.raises(ValidationError):
            RunSettings(concurrency=value)

    @pytest.mark.parametrize("value", [0.0, -1.0, 3601.0])
    def test_a_case_timeout_is_bounded(self, value: float):
        # Zero would mean every case times out immediately, which looks like a
        # model outage rather than a configuration error.
        with pytest.raises(ValidationError):
            RunSettings(case_timeout_s=value)

    @pytest.mark.parametrize("value", [0, 101])
    def test_the_sample_count_is_bounded(self, value: int):
        with pytest.raises(ValidationError):
            RunSettings(samples=value)

    def test_the_log_level_is_a_closed_set(self):
        with pytest.raises(ValidationError):
            LogSettings(level="TRACE")  # type: ignore[arg-type]

    def test_the_log_format_is_a_closed_set(self):
        with pytest.raises(ValidationError):
            LogSettings(format="xml")  # type: ignore[arg-type]

    def test_settings_can_still_be_built_directly(self):
        # Used by tests and by anything embedding the runner; if this stops
        # working the library is only usable through the command line.
        settings = Settings(run=RunSettings(concurrency=3), log=LogSettings(level="ERROR"))
        assert settings.run.concurrency == 3
        assert settings.log.level == "ERROR"

"""The suite model, the loader and the digest."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from aievals.errors import SuiteError
from aievals.suite import load_suite, parse_suite, suite_digest
from aievals.suite.digest import digest_payload
from aievals.suite.loader import MAX_SUITE_BYTES
from aievals.suite.models import Case, GraderSpec, ProviderSpec, Suite, Thresholds

pytestmark = pytest.mark.unit

MINIMAL = textwrap.dedent(
    """
    name: minimal
    cases:
      - id: one
        prompt: hello
    """
)


def suite_of(cases: tuple[Case, ...], **kwargs) -> Suite:
    return Suite(name="s", cases=cases, **kwargs)


class TestModels:
    def test_a_suite_needs_at_least_one_case(self):
        with pytest.raises(ValidationError, match="passes every gate trivially"):
            Suite(name="empty", cases=())

    def test_duplicate_case_ids_are_refused(self):
        with pytest.raises(ValidationError, match="more than once"):
            suite_of((Case(id="a", prompt="x"), Case(id="a", prompt="y")))

    @pytest.mark.parametrize("bad", ["", "-leading", "with space", "a" * 200, "sla/sh"])
    def test_unusable_case_ids_are_refused(self, bad: str):
        with pytest.raises(ValidationError):
            Case(id=bad, prompt="x")

    def test_an_empty_prompt_is_refused(self):
        with pytest.raises(ValidationError):
            Case(id="a", prompt="")

    def test_an_unknown_key_is_an_error_not_a_silent_drop(self):
        # A mistyped key would otherwise remove an expectation while the gate
        # kept reporting success.
        with pytest.raises(ValidationError):
            Case.model_validate({"id": "a", "prompt": "x", "graderz": []})

    def test_a_grader_name_must_look_like_an_identifier(self):
        with pytest.raises(ValidationError, match="not a grader name"):
            GraderSpec(type="Contains-All")

    def test_a_provider_name_must_look_like_an_identifier(self):
        with pytest.raises(ValidationError, match="not a provider name"):
            ProviderSpec(name="OpenAI Compatible")

    def test_a_bad_tag_is_refused(self):
        with pytest.raises(ValidationError, match="not a usable tag"):
            Case(id="a", prompt="x", tags=("has space",))

    def test_thresholds_reject_an_out_of_range_pass_rate(self):
        with pytest.raises(ValidationError):
            Thresholds(min_pass_rate=1.5)

    def test_thresholds_reject_an_alpha_outside_the_open_unit_interval(self):
        with pytest.raises(ValidationError):
            Thresholds(alpha=0.0)

    def test_models_are_immutable(self):
        case = Case(id="a", prompt="x")
        with pytest.raises(ValidationError):
            case.prompt = "y"

    def test_default_graders_come_first(self):
        suite = suite_of(
            (Case(id="a", prompt="x", graders=(GraderSpec(type="exact"),)),),
            default_graders=(GraderSpec(type="regex"),),
        )
        assert [spec.type for spec in suite.graders_for(suite.cases[0])] == ["regex", "exact"]

    def test_case_by_id_finds_and_misses(self):
        suite = suite_of((Case(id="a", prompt="x"),))
        assert suite.case_by_id("a") is not None
        assert suite.case_by_id("b") is None


class TestParsing:
    def test_a_minimal_suite_parses(self):
        suite = parse_suite(MINIMAL)
        assert suite.name == "minimal"
        assert len(suite.cases) == 1

    def test_json_is_accepted_as_well_as_yaml(self):
        suite = parse_suite('{"name": "j", "cases": [{"id": "a", "prompt": "p"}]}')
        assert suite.name == "j"

    def test_an_empty_document_is_refused(self):
        with pytest.raises(SuiteError, match="is empty"):
            parse_suite("")

    def test_a_top_level_list_is_refused(self):
        with pytest.raises(SuiteError, match="mapping at the top level"):
            parse_suite("- a\n- b\n")

    def test_malformed_yaml_is_refused_with_the_parser_message(self):
        with pytest.raises(SuiteError, match="not valid YAML"):
            parse_suite("name: [unclosed\n")

    def test_a_validation_failure_names_the_field(self):
        with pytest.raises(SuiteError, match="cases"):
            parse_suite("name: x\n")

    def test_an_unknown_grader_is_refused_at_load_time(self):
        text = MINIMAL + "    graders:\n      - type: vibes\n"
        with pytest.raises(SuiteError, match="unknown grader"):
            parse_suite(text)

    def test_the_refusal_lists_the_graders_that_do_exist(self):
        text = MINIMAL + "    graders:\n      - type: vibes\n"
        with pytest.raises(SuiteError) as caught:
            parse_suite(text)
        # The remedy, not the message: an error that says only "unknown" leaves
        # the reader to go and find the list themselves.
        assert "contains_all" in caught.value.remedy
        assert "not imported" in caught.value.remedy

    def test_a_misconfigured_grader_is_refused_with_the_case_id(self):
        text = textwrap.dedent(
            """
            name: bad
            cases:
              - id: broken
                prompt: hello
                graders:
                  - type: regex
                    params:
                      pattern: "("
            """
        )
        with pytest.raises(SuiteError, match="'broken'"):
            parse_suite(text)

    def test_a_default_grader_is_validated_too(self):
        text = textwrap.dedent(
            """
            name: bad
            default_graders:
              - type: contains_all
                params:
                  values: "a bare string"
            cases:
              - id: one
                prompt: hello
            """
        )
        with pytest.raises(SuiteError, match="list of strings"):
            parse_suite(text)


class TestLoading:
    def test_a_file_loads(self, tmp_path: Path):
        path = tmp_path / "s.yaml"
        path.write_text(MINIMAL, encoding="utf-8")
        assert load_suite(path).name == "minimal"

    def test_a_missing_file_is_reported_with_the_path(self, tmp_path: Path):
        with pytest.raises(SuiteError, match="could not be opened"):
            load_suite(tmp_path / "absent.yaml")

    def test_an_unexpected_suffix_is_refused(self, tmp_path: Path):
        path = tmp_path / "s.txt"
        path.write_text(MINIMAL, encoding="utf-8")
        with pytest.raises(SuiteError, match="does not look like a suite file"):
            load_suite(path)

    def test_an_oversized_file_is_refused_before_parsing(self, tmp_path: Path):
        path = tmp_path / "s.yaml"
        path.write_text("# padding\n" * (MAX_SUITE_BYTES // 10 + 1), encoding="utf-8")
        with pytest.raises(SuiteError, match="over the"):
            load_suite(path)

    def test_invalid_utf8_is_refused(self, tmp_path: Path):
        path = tmp_path / "s.yaml"
        path.write_bytes(b"name: \xff\xfe\ncases: []\n")
        with pytest.raises(SuiteError, match="not valid UTF-8"):
            load_suite(path)


class TestDigest:
    def test_the_digest_is_stable_across_calls(self):
        suite = parse_suite(MINIMAL)
        assert suite_digest(suite) == suite_digest(suite)

    def test_the_digest_has_the_expected_shape(self):
        assert suite_digest(parse_suite(MINIMAL)).startswith("sha256:")

    def test_reordering_cases_does_not_change_the_digest(self):
        one = parse_suite("name: s\ncases:\n  - {id: a, prompt: x}\n  - {id: b, prompt: y}\n")
        other = parse_suite("name: s\ncases:\n  - {id: b, prompt: y}\n  - {id: a, prompt: x}\n")
        assert suite_digest(one) == suite_digest(other)

    def test_editing_prose_does_not_change_the_digest(self):
        # Otherwise every comment edit invalidates a baseline, people pass
        # --allow-stale-baseline, and the check stops existing.
        plain = parse_suite("name: s\ncases:\n  - {id: a, prompt: x}\n")
        annotated = parse_suite(
            "name: s\ndescription: a note\ncases:\n"
            "  - {id: a, prompt: x, rationale: because, tags: [t]}\n"
        )
        assert suite_digest(plain) == suite_digest(annotated)

    def test_changing_a_prompt_changes_the_digest(self):
        one = parse_suite("name: s\ncases:\n  - {id: a, prompt: x}\n")
        other = parse_suite("name: s\ncases:\n  - {id: a, prompt: y}\n")
        assert suite_digest(one) != suite_digest(other)

    def test_changing_a_grader_parameter_changes_the_digest(self):
        base = "name: s\ncases:\n  - id: a\n    prompt: x\n    graders:\n"
        one = parse_suite(base + "      - {type: exact, params: {value: a}}\n")
        other = parse_suite(base + "      - {type: exact, params: {value: b}}\n")
        assert suite_digest(one) != suite_digest(other)

    def test_changing_a_threshold_changes_the_digest(self):
        one = parse_suite("name: s\ncases:\n  - {id: a, prompt: x}\n")
        other = parse_suite(
            "name: s\nthresholds: {min_pass_rate: 0.9}\ncases:\n  - {id: a, prompt: x}\n"
        )
        assert suite_digest(one) != suite_digest(other)

    def test_changing_the_model_changes_the_digest(self):
        one = parse_suite("name: s\ncases:\n  - {id: a, prompt: x}\n")
        other = parse_suite(
            "name: s\nprovider: {name: replay, model: m}\ncases:\n  - {id: a, prompt: x}\n"
        )
        assert suite_digest(one) != suite_digest(other)

    def test_the_payload_is_inspectable(self):
        payload = digest_payload(parse_suite(MINIMAL))
        assert payload["name"] == "minimal"
        assert "rationale" not in payload["cases"][0]

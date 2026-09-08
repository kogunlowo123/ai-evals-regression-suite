"""The three report shapes."""

from __future__ import annotations

import json
from xml.etree import ElementTree

import pytest

from aievals.gate import evaluate
from aievals.mutation import MutationHarness
from aievals.report import json_report, junit, markdown
from aievals.suite import parse_suite
from tests.conftest import FAKE_AWS_KEY, RecordingProvider

pytestmark = pytest.mark.integration

SUITE = """
name: reported
provider: {name: scripted}
thresholds: {min_pass_rate: 1.0}
cases:
  - {id: good, prompt: pg, graders: [{type: contains_all, params: {values: [ok]}}]}
  - {id: bad, prompt: pb, graders: [{type: contains_all, params: {values: [ok]}}]}
"""

ANSWERS = {"pg": "ok fine", "pb": "not what was wanted"}


@pytest.fixture
async def report(run_suite):
    suite = parse_suite(SUITE)
    run = await run_suite(suite, RecordingProvider(ANSWERS))
    return evaluate(run, suite)


class TestJsonReport:
    async def test_it_is_valid_json_with_the_verdict_at_the_top(self, report):
        payload = json.loads(json_report.render(report))
        assert payload["passed"] is False
        assert payload["schema"] == "aievals.report/1"
        assert payload["tool"].startswith("aievals ")

    async def test_responses_are_omitted_by_default(self, report):
        # A report is an artefact. Responses are derived from prompts that may
        # carry personal data, and embedding them by default puts that data
        # somewhere nobody decided to put it.
        rendered = json_report.render(report)
        assert "not what was wanted" not in rendered
        payload = json.loads(rendered)
        omitted = payload["run"]["cases"][0]["samples"][0]["response_omitted"]
        assert omitted["length"] > 0
        assert len(omitted["sha256"]) == 16

    async def test_responses_can_be_included_deliberately(self, report):
        rendered = json_report.render(report, include_responses=True)
        assert "not what was wanted" in rendered

    async def test_the_digest_distinguishes_two_different_responses(self, run_suite):
        suite = parse_suite(SUITE)
        one = evaluate(await run_suite(suite, RecordingProvider(ANSWERS)), suite)
        other = evaluate(
            await run_suite(suite, RecordingProvider({**ANSWERS, "pb": "something else"})),
            suite,
        )
        first = json.loads(json_report.render(one))["run"]["cases"][1]["samples"][0]
        second = json.loads(json_report.render(other))["run"]["cases"][1]["samples"][0]
        assert first["response_omitted"]["sha256"] != second["response_omitted"]["sha256"]

    async def test_a_credential_in_an_included_response_is_redacted(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(
            suite, RecordingProvider({**ANSWERS, "pg": f"ok, use {FAKE_AWS_KEY}"})
        )
        rendered = json_report.render(evaluate(run, suite), include_responses=True)
        assert FAKE_AWS_KEY not in rendered
        assert json.loads(rendered)["redaction"]["applied"] is True

    async def test_a_credential_quoted_in_a_grader_detail_is_redacted(self, run_suite):
        # The leak this shape of code produced once before: a derived string
        # attached after the pass carries what the pass removed.
        suite = parse_suite(
            "name: s\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: exact, params: {value: expected}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": f"actual {FAKE_AWS_KEY}"}))
        rendered = json_report.render(evaluate(run, suite))
        assert FAKE_AWS_KEY not in rendered
        assert "REDACTED:aws_access_key_id" in rendered

    async def test_a_mutation_report_can_be_attached(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": "ok"}))
        mutation = await MutationHarness().analyse(suite, run)
        payload = json.loads(json_report.render(evaluate(run, suite), mutation=mutation))
        assert payload["mutation"]["total"] > 0

    async def test_mutation_excerpts_are_omitted_with_responses(self, run_suite):
        suite = parse_suite(
            "name: weak\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_none, params: {values: [never appears]}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": "a long enough answer to cut"}))
        mutation = await MutationHarness().analyse(suite, run)
        payload = json.loads(json_report.render(evaluate(run, suite), mutation=mutation))
        assert "excerpt" not in payload["mutation"]["mutants"][0]


class TestJUnit:
    async def test_it_parses_as_xml(self, report):
        root = ElementTree.fromstring(junit.render(report))
        assert root.tag == "testsuite"

    async def test_each_case_becomes_a_test_case(self, report):
        root = ElementTree.fromstring(junit.render(report))
        names = {element.get("name") for element in root.findall("testcase")}
        assert {"good", "bad"} <= names

    async def test_a_failing_case_carries_a_failure_element(self, report):
        root = ElementTree.fromstring(junit.render(report))
        bad = next(e for e in root.findall("testcase") if e.get("name") == "bad")
        failure = bad.find("failure")
        assert failure is not None
        assert "contains_all" in (failure.get("message") or "")

    async def test_a_gate_finding_with_no_case_gets_a_synthetic_test_case(self, report):
        root = ElementTree.fromstring(junit.render(report))
        gate = [e for e in root.findall("testcase") if e.get("classname") == "gate"]
        assert [e.get("name") for e in gate] == ["PASS_RATE_BELOW_MINIMUM"]

    async def test_properties_record_the_run_conditions(self, report):
        root = ElementTree.fromstring(junit.render(report))
        properties = root.find("properties")
        assert properties is not None
        names = {p.get("name") for p in properties.findall("property")}
        assert {"suite_digest", "provider", "hermetic", "pass_rate"} <= names

    async def test_control_characters_are_stripped_so_the_file_parses(self, run_suite):
        # A model response with a stray control character would otherwise
        # produce an XML document the CI silently discards.
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": "bad\x00\x1b[31m"}))
        document = junit.render(evaluate(run, suite))
        ElementTree.fromstring(document)
        assert "\x00" not in document

    async def test_a_credential_in_a_detail_is_redacted(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": FAKE_AWS_KEY}))
        assert FAKE_AWS_KEY not in junit.render(evaluate(run, suite))

    async def test_an_errored_case_becomes_an_error_element(self, run_suite):
        from aievals.errors import ProviderError
        from aievals.providers.base import Provider

        class Broken(Provider):
            name = "broken"
            reaches_network = False

            async def complete(self, request):
                raise ProviderError("no model here")

        suite = parse_suite(SUITE)
        run = await run_suite(suite, Broken())
        root = ElementTree.fromstring(junit.render(evaluate(run, suite)))
        assert root.findall("testcase/error")


class TestMarkdown:
    async def test_the_verdict_is_the_first_line(self, report):
        assert markdown.render(report).splitlines()[0] == "# Evaluation gate: FAILED"

    async def test_a_passing_run_says_so(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": "ok"}))
        assert "passed" in markdown.render(evaluate(run, suite)).splitlines()[0]

    async def test_the_reasons_come_before_the_tables(self, report):
        text = markdown.render(report)
        assert text.index("Why the build is red") < text.index("Failing cases")

    async def test_failing_cases_are_tabulated_with_a_detail(self, report):
        text = markdown.render(report)
        assert "| `bad` |" in text
        assert "contains_all" in text

    async def test_a_pipe_in_a_detail_does_not_break_the_table(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": "a | b | c"}))
        row = next(
            line
            for line in markdown.render(evaluate(run, suite)).splitlines()
            if line.startswith("| `bad`")
        )
        assert row.count("|") == 4

    async def test_a_credential_is_redacted(self, run_suite):
        suite = parse_suite(SUITE)
        run = await run_suite(suite, RecordingProvider({"pg": "ok", "pb": FAKE_AWS_KEY}))
        assert FAKE_AWS_KEY not in markdown.render(evaluate(run, suite))

    async def test_a_mutation_section_lists_survivors(self, run_suite):
        suite = parse_suite(
            "name: weak\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_none, params: {values: [never appears]}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": "a long enough answer to cut"}))
        mutation = await MutationHarness().analyse(suite, run)
        text = markdown.render(evaluate(run, suite), mutation=mutation)
        assert "Suite quality (meta-gate)" in text
        assert "surviving mutant" in text

    async def test_a_clean_mutation_section_shows_the_table(self, run_suite):
        suite = parse_suite(
            "name: strong\nprovider: {name: scripted}\n"
            "cases:\n  - {id: a, prompt: p, graders: "
            "[{type: contains_all, params: {values: ['a full and specific answer']}}]}\n"
        )
        run = await run_suite(suite, RecordingProvider({"p": "a full and specific answer"}))
        mutation = await MutationHarness().analyse(suite, run)
        text = markdown.render(evaluate(run, suite), mutation=mutation)
        assert "| Corruption | Caught |" in text

    async def test_the_footer_records_the_conditions(self, report):
        text = markdown.render(report)
        assert "hermetic:" in text
        assert "suite digest" in text

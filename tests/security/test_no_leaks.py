"""Credentials must not leave the process through an artefact.

The specific failure this file guards against has happened before in this
series: redaction was applied to the *inputs*, and a string derived from those
inputs — an excerpt, a grader detail, a diff — was attached to the result
afterwards, carrying what the pass had removed. Everything below asserts against
the **whole serialised artefact**, not against one member of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aievals.gate import evaluate
from aievals.mutation import MutationHarness
from aievals.providers.base import Completion, CompletionRequest
from aievals.providers.cassette import Cassette
from aievals.report import json_report, junit, markdown
from aievals.suite import parse_suite
from tests.conftest import (
    FAKE_AWS_KEY,
    FAKE_GITHUB_TOKEN,
    FAKE_OPENAI_KEY,
    RecordingProvider,
)

pytestmark = pytest.mark.security

SECRETS = [FAKE_AWS_KEY, FAKE_GITHUB_TOKEN, FAKE_OPENAI_KEY]

EXACT_SUITE = """
name: leaky
provider: {name: scripted}
thresholds: {min_pass_rate: 1.0}
cases:
  - {id: a, prompt: p, graders: [{type: exact, params: {value: "the expected answer"}}]}
"""

WEAK_SUITE = """
name: weak
provider: {name: scripted}
cases:
  - {id: a, prompt: p, graders: [{type: contains_none, params: {values: ["never appears here"]}}]}
"""


@pytest.mark.parametrize("secret", SECRETS)
class TestReportsDoNotCarryCredentials:
    async def test_the_json_report_is_clean_when_a_response_holds_one(self, run_suite, secret: str):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"here it is: {secret}"}))
        rendered = json_report.render(evaluate(run, suite), include_responses=True)
        assert secret not in rendered

    async def test_a_grader_detail_quoting_the_response_is_clean(self, run_suite, secret: str):
        # `exact` echoes the response it rejected into its detail. That string
        # is derived from the response and attached after it, which is the
        # exact shape of the leak this guards.
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"wrong: {secret}"}))
        rendered = json_report.render(evaluate(run, suite))
        assert secret not in rendered
        assert "REDACTED" in rendered

    async def test_the_junit_document_is_clean(self, run_suite, secret: str):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"wrong: {secret}"}))
        assert secret not in junit.render(evaluate(run, suite))

    async def test_the_markdown_summary_is_clean(self, run_suite, secret: str):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"wrong: {secret}"}))
        assert secret not in markdown.render(evaluate(run, suite))

    async def test_a_mutation_excerpt_is_clean(self, run_suite, secret: str):
        # A survivor's excerpt is built from the mutated response and attached
        # to the report after the run. Same shape, same guard.
        suite = parse_suite(WEAK_SUITE)
        run = await run_suite(
            suite, RecordingProvider({"p": f"a long enough answer holding {secret} in it"})
        )
        mutation = await MutationHarness().analyse(suite, run)
        assert mutation.survivors
        rendered = json_report.render(
            evaluate(run, suite), include_responses=True, mutation=mutation
        )
        assert secret not in rendered

    def test_a_cassette_written_to_disk_is_clean(self, tmp_path: Path, secret: str):
        # Cassettes are committed to repositories.
        request = CompletionRequest(prompt="p", model="m")
        cassette = Cassette()
        cassette.put(request, Completion(text=f"use {secret}", model="m", provider="p"))
        written = cassette.save(tmp_path / "c.json").read_text(encoding="utf-8")
        assert secret not in written


class TestRedactionIsAppliedLast:
    async def test_the_report_records_that_it_redacted(self, run_suite):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"wrong: {FAKE_AWS_KEY}"}))
        payload = json.loads(json_report.render(evaluate(run, suite)))
        assert payload["redaction"]["applied"] is True
        assert "aws_access_key_id" in payload["redaction"]["rules"]

    async def test_a_clean_run_reports_no_redaction(self, run_suite):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": "the expected answer"}))
        payload = json.loads(json_report.render(evaluate(run, suite)))
        assert payload["redaction"]["applied"] is False

    async def test_field_names_survive_redaction(self, run_suite):
        # A pass that redacted keys as well as values would produce a report
        # nothing can read.
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": f"wrong {FAKE_AWS_KEY}"}))
        payload = json.loads(json_report.render(evaluate(run, suite)))
        assert "run" in payload
        assert "pass_rate" in payload["run"]


class TestPrivacyOfPrompts:
    """The precise claim, not a comfortable one.

    ``--include-responses`` off means the *full response* is replaced by a
    length and a digest. A grader detail may still quote a bounded fragment,
    because that fragment is the reason the build is red. These tests pin both
    halves so the documentation cannot drift away from the behaviour.
    """

    LONG_RESPONSE = (
        "The patient's name is Alex Chandra and the record number is 88213. "
        "The consultation covered a follow-up appointment, a repeat prescription "
        "and a referral to the cardiology department at the district hospital."
    )

    async def test_the_full_response_is_not_in_the_default_report(self, run_suite):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": self.LONG_RESPONSE}))
        rendered = json_report.render(evaluate(run, suite))
        assert self.LONG_RESPONSE not in rendered
        assert "cardiology department" not in rendered

    async def test_a_quoted_fragment_is_bounded(self, run_suite):
        from aievals.graders.text import MAX_QUOTED_RESPONSE

        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": self.LONG_RESPONSE}))
        detail = run.case("a").samples[0].grades[0].detail
        quoted = detail.split("got ", 1)[1]
        assert len(quoted) <= MAX_QUOTED_RESPONSE + 4

    async def test_the_response_is_replaced_by_a_length_and_a_digest(self, run_suite):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": self.LONG_RESPONSE}))
        payload = json.loads(json_report.render(evaluate(run, suite)))
        sample = payload["run"]["cases"][0]["samples"][0]
        assert "response" not in sample
        assert sample["response_omitted"]["length"] == len(self.LONG_RESPONSE)

    async def test_including_responses_is_an_explicit_choice(self, run_suite):
        suite = parse_suite(EXACT_SUITE)
        run = await run_suite(suite, RecordingProvider({"p": self.LONG_RESPONSE}))
        rendered = json_report.render(evaluate(run, suite), include_responses=True)
        assert "cardiology department" in rendered

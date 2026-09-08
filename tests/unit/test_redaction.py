"""Credential redaction.

Every fixture here is built by concatenation so that nothing in this file was
ever a valid credential, and a reader can see that at a glance.
"""

from __future__ import annotations

import pytest

from aievals.redaction import RULES, redact_structure, redact_text
from tests.conftest import FAKE_AWS_KEY, FAKE_GITHUB_TOKEN, FAKE_OPENAI_KEY

pytestmark = pytest.mark.unit


class TestRules:
    def test_every_rule_has_a_distinct_name(self):
        names = [rule.name for rule in RULES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize(
        ("text", "rule"),
        [
            (FAKE_AWS_KEY, "aws_access_key_id"),
            (FAKE_GITHUB_TOKEN, "github_token"),
            ("github_pat_" + "b" * 30, "github_fine_grained_token"),
            (FAKE_OPENAI_KEY, "openai_api_key"),
            ("sk-ant-" + "c" * 40, "anthropic_api_key"),
            ("AIza" + "d" * 35, "google_api_key"),
            ("xoxb-" + "1" * 20, "slack_token"),
            ("sk_live_" + "e" * 24, "stripe_key"),
            ("Authorization: Bearer " + "f" * 40, "bearer_token"),
            ("https://user:hunter2pass@example.com/x", "basic_auth_url"),
            ("api_key = " + "g" * 32, "assigned_secret"),
        ],
    )
    def test_each_shape_is_recognised(self, text: str, rule: str):
        cleaned, result = redact_text(text)
        assert rule in result.rules
        assert result.count >= 1
        assert f"[REDACTED:{rule}]" in cleaned

    def test_a_jwt_is_recognised(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "h" * 20
        _, result = redact_text(token)
        assert "jwt" in result.rules

    def test_a_private_key_block_is_removed_whole(self):
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\nlines\n-----END RSA PRIVATE KEY-----"
        cleaned, result = redact_text(block)
        assert "private_key_block" in result.rules
        assert "MIIB" not in cleaned

    def test_the_placeholder_does_not_echo_the_length(self):
        cleaned, _ = redact_text("ghp_" + "a" * 200)
        assert len(cleaned) < 60

    def test_surrounding_context_survives_a_group_replacement(self):
        cleaned, _ = redact_text("Authorization: Bearer " + "z" * 40)
        assert cleaned.startswith("Authorization: Bearer ")


class TestFalsePositives:
    @pytest.mark.parametrize(
        "text",
        [
            "password = REPLACE_ME",
            "api_key: ${OPENAI_API_KEY}",
            "token = <your-token-here>",
            "secret: xxxxxxxxxxxxxxx",
            "api_key = your-api-key-here",
            "password = placeholder",
            "api_key: smoke-test-key-not-a-real-secret",
        ],
    )
    def test_documented_placeholders_are_not_reported(self, text: str):
        # Reporting these trains reviewers to dismiss findings, which is how a
        # real one gets waved through.
        _, result = redact_text(text)
        assert "assigned_secret" not in result.rules

    @pytest.mark.parametrize(
        "text",
        [
            "the response was 42 characters long",
            "we are using the openai library",
            "see the token bucket algorithm",
        ],
    )
    def test_ordinary_prose_is_untouched(self, text: str):
        cleaned, result = redact_text(text)
        assert cleaned == text
        assert not result.applied


class TestStructureWalk:
    def test_it_reaches_nested_values(self):
        payload = {"a": [{"b": {"c": FAKE_AWS_KEY}}]}
        cleaned, result = redact_structure(payload)
        assert result.count == 1
        assert FAKE_AWS_KEY not in str(cleaned)

    def test_dictionary_keys_are_not_redacted(self):
        # Redacting keys produces a report nothing can read.
        payload = {"api_key": "ordinary value"}
        cleaned, _ = redact_structure(payload)
        assert "api_key" in cleaned

    def test_tuples_and_lists_keep_their_type(self):
        cleaned, _ = redact_structure({"t": ("a",), "l": ["b"]})
        assert isinstance(cleaned["t"], tuple)
        assert isinstance(cleaned["l"], list)

    def test_non_string_leaves_pass_through(self):
        cleaned, result = redact_structure({"n": 1, "b": True, "z": None})
        assert cleaned == {"n": 1, "b": True, "z": None}
        assert not result.applied

    def test_the_rule_names_are_reported(self):
        _, result = redact_structure({"a": FAKE_AWS_KEY, "b": FAKE_GITHUB_TOKEN})
        assert result.rules == frozenset({"aws_access_key_id", "github_token"})

    def test_the_summary_serialises(self):
        _, result = redact_structure({"a": FAKE_AWS_KEY})
        payload = result.as_dict()
        assert payload["applied"] is True
        assert payload["rules"] == ["aws_access_key_id"]

    def test_a_clean_structure_reports_nothing_applied(self):
        _, result = redact_structure({"a": "nothing here"})
        assert result.as_dict() == {"applied": False, "count": 0, "rules": []}

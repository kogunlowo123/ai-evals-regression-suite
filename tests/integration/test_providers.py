"""Providers: the offline three, cassettes, and the HTTP one against loopback.

The HTTP provider is exercised against a fake server on ``127.0.0.1`` so that
request construction, authentication, retry, backoff, error mapping and usage
parsing all run under test. What never happens in CI is a request to a vendor.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aievals.errors import CassetteError, HermeticViolationError, ProviderError
from aievals.providers import build_provider, registered_names
from aievals.providers.base import Completion, CompletionRequest
from aievals.providers.cassette import CASSETTE_VERSION, Cassette
from aievals.providers.offline import EchoProvider, ReplayProvider, ScriptedProvider
from aievals.providers.openai_compat import OpenAICompatibleProvider
from aievals.suite.models import ProviderSpec
from tests.conftest import FAKE_OPENAI_KEY

pytestmark = pytest.mark.integration

REQUEST = CompletionRequest(prompt="hello", model="m", system="be brief")


# -- a fake OpenAI-compatible server -------------------------------------


class FakeHandler(BaseHTTPRequestHandler):
    """Answers /chat/completions from a script the test sets on the class."""

    script: ClassVar[list[tuple[int, dict[str, Any] | str]]] = []
    seen: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeHandler.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        status, payload = FakeHandler.script.pop(0) if FakeHandler.script else (200, _ok("hi"))
        encoded = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


def _ok(text: str, **usage: int) -> dict[str, Any]:
    return {
        "model": "server-model",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 4, "completion_tokens": 6},
    }


@pytest.fixture
def fake_server():
    """A loopback HTTP server, scripted per test."""
    FakeHandler.script = []
    FakeHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- offline providers ---------------------------------------------------


class TestRegistry:
    def test_every_built_in_provider_is_registered(self):
        assert set(registered_names()) == {"echo", "openai_compatible", "replay", "scripted"}

    def test_an_unknown_provider_names_the_known_ones(self):
        with pytest.raises(ProviderError) as caught:
            build_provider(ProviderSpec(name="nonexistent"))
        assert "replay" in caught.value.remedy

    def test_registering_a_duplicate_name_is_refused(self):
        from aievals.providers.registry import provider

        with pytest.raises(ProviderError, match="already registered"):

            @provider("echo")
            class Second(EchoProvider):
                pass


class TestEcho:
    async def test_it_returns_the_prompt(self):
        result = await EchoProvider().complete(REQUEST)
        assert result.text == "hello"

    async def test_a_prefix_is_applied(self):
        result = await EchoProvider({"prefix": ">> "}).complete(REQUEST)
        assert result.text == ">> hello"

    def test_it_declares_no_egress(self):
        assert EchoProvider.reaches_network is False
        assert "no egress" in EchoProvider({}).describe()


class TestScripted:
    async def test_a_mapping_answers_by_prompt(self):
        result = await ScriptedProvider({"responses": {"hello": "world"}}).complete(REQUEST)
        assert result.text == "world"

    async def test_a_list_is_consumed_in_order(self):
        used = ScriptedProvider({"responses": ["one", "two"]})
        assert (await used.complete(REQUEST)).text == "one"
        assert (await used.complete(REQUEST)).text == "two"

    async def test_running_out_of_queued_responses_is_an_error(self):
        used = ScriptedProvider({"responses": ["only"]})
        await used.complete(REQUEST)
        with pytest.raises(ProviderError, match="ran out"):
            await used.complete(REQUEST)

    async def test_an_unmapped_prompt_is_an_error(self):
        with pytest.raises(ProviderError, match="no response for"):
            await ScriptedProvider({"responses": {"other": "x"}}).complete(REQUEST)

    async def test_a_callable_receives_the_request(self):
        used = ScriptedProvider({"respond": lambda request: request.prompt.upper()})
        assert (await used.complete(REQUEST)).text == "HELLO"

    def test_both_forms_at_once_are_refused(self):
        with pytest.raises(ProviderError, match="not both"):
            ScriptedProvider({"respond": lambda r: "", "responses": {}})

    def test_neither_form_is_refused(self):
        with pytest.raises(ProviderError, match="needs"):
            ScriptedProvider({})


class TestCassette:
    def test_a_recording_round_trips(self, tmp_path: Path):
        cassette = Cassette()
        cassette.put(REQUEST, Completion(text="answer", model="m", provider="p"))
        path = cassette.save(tmp_path / "c.json")
        assert Cassette.load(path).get(REQUEST).text == "answer"

    def test_a_replayed_completion_is_marked_as_such(self, tmp_path: Path):
        cassette = Cassette()
        cassette.put(REQUEST, Completion(text="answer", model="m", provider="p"))
        assert cassette.get(REQUEST).replayed is True

    def test_a_miss_is_an_error_rather_than_a_live_call(self):
        # A replay that quietly reaches the network is a hermetic run that is
        # not hermetic.
        with pytest.raises(CassetteError, match="no recording"):
            Cassette().get(REQUEST)

    def test_the_miss_explains_what_invalidates_a_recording(self):
        with pytest.raises(CassetteError) as caught:
            Cassette().get(REQUEST)
        assert "temperature" in caught.value.remedy

    @pytest.mark.parametrize(
        "changed",
        [
            {"prompt": "different"},
            {"model": "other"},
            {"system": "other"},
            {"temperature": 0.9},
            {"max_tokens": 99},
        ],
    )
    def test_any_change_that_could_change_the_answer_invalidates_the_key(
        self, changed: dict[str, Any]
    ):
        cassette = Cassette()
        cassette.put(REQUEST, Completion(text="answer", model="m", provider="p"))
        from dataclasses import replace

        with pytest.raises(CassetteError):
            cassette.get(replace(REQUEST, **changed))

    def test_a_missing_file_is_reported_with_a_remedy(self, tmp_path: Path):
        with pytest.raises(CassetteError, match="could not be opened"):
            Cassette.load(tmp_path / "absent.json")

    def test_a_wrong_version_is_refused(self, tmp_path: Path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"cassette_version": 99, "entries": []}), encoding="utf-8")
        with pytest.raises(CassetteError, match="version"):
            Cassette.load(path)

    def test_a_malformed_entry_is_refused(self, tmp_path: Path):
        path = tmp_path / "c.json"
        path.write_text(
            json.dumps({"cassette_version": CASSETTE_VERSION, "entries": [{"no": "id"}]}),
            encoding="utf-8",
        )
        with pytest.raises(CassetteError, match="no fingerprint"):
            Cassette.load(path)

    def test_a_credential_in_a_completion_is_redacted_before_it_is_written(self, tmp_path: Path):
        # Cassettes are committed. This is the property that makes that safe.
        cassette = Cassette()
        cassette.put(
            REQUEST,
            Completion(text=f"use {FAKE_OPENAI_KEY} to authenticate", model="m", provider="p"),
        )
        path = cassette.save(tmp_path / "c.json")
        written = path.read_text(encoding="utf-8")
        assert FAKE_OPENAI_KEY not in written
        assert "REDACTED:openai_api_key" in written
        assert json.loads(written)["redaction"]["applied"] is True


class TestReplayProvider:
    async def test_it_serves_from_a_loaded_cassette(self):
        cassette = Cassette()
        cassette.put(REQUEST, Completion(text="recorded", model="m", provider="p"))
        provider = ReplayProvider({"cassette": cassette})
        assert (await provider.complete(REQUEST)).text == "recorded"

    def test_it_loads_from_a_path(self, tmp_path: Path):
        cassette = Cassette()
        cassette.put(REQUEST, Completion(text="recorded", model="m", provider="p"))
        path = cassette.save(tmp_path / "c.json")
        assert len(ReplayProvider({"cassette": str(path)})._cassette) == 1

    def test_it_reports_how_many_recordings_it_holds(self):
        assert "0 recording" in ReplayProvider({"cassette": Cassette()}).describe()

    def test_a_missing_cassette_option_is_refused(self):
        with pytest.raises(ProviderError, match="needs a cassette"):
            ReplayProvider({})

    def test_a_nonsense_cassette_option_is_refused(self):
        with pytest.raises(ProviderError, match="must be a path"):
            ReplayProvider({"cassette": 42})


class TestHermeticMode:
    def test_a_network_provider_cannot_be_constructed_in_hermetic_mode(self):
        spec = ProviderSpec(name="openai_compatible", options={"base_url": "https://x/v1"})
        with pytest.raises(HermeticViolationError, match="hermetic"):
            build_provider(spec, hermetic=True)

    def test_the_refusal_explains_the_alternative(self):
        spec = ProviderSpec(name="openai_compatible", options={"base_url": "https://x/v1"})
        with pytest.raises(HermeticViolationError) as caught:
            build_provider(spec, hermetic=True)
        assert "aievals record" in caught.value.remedy

    def test_offline_providers_are_unaffected(self):
        assert build_provider(ProviderSpec(name="echo"), hermetic=True) is not None

    def test_the_default_for_an_undeclared_provider_is_restrictive(self):
        from aievals.providers.base import Provider

        # A new provider that forgets to declare the flag must be refused
        # rather than admitted.
        assert Provider.reaches_network is True


class TestOpenAICompatible:
    def test_a_credential_literal_in_the_suite_is_refused(self):
        # Suite files are committed; there is no code path that accepts a key.
        with pytest.raises(ProviderError, match="not accepted here"):
            OpenAICompatibleProvider({"base_url": "https://x/v1", "api_key": "sk-live"})

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://x"])
    def test_a_non_http_scheme_is_refused(self, url: str):
        with pytest.raises(ProviderError, match="allowed URL scheme"):
            OpenAICompatibleProvider({"base_url": url})

    def test_a_url_with_no_host_is_refused(self):
        with pytest.raises(ProviderError, match="no host"):
            OpenAICompatibleProvider({"base_url": "http:///v1"})

    def test_a_missing_base_url_is_refused(self, monkeypatch):
        monkeypatch.delenv("AIEVALS_BASE_URL", raising=False)
        with pytest.raises(ProviderError, match="needs a base URL"):
            OpenAICompatibleProvider({})

    async def test_a_missing_credential_is_reported_by_variable_name(
        self, fake_server: str, monkeypatch
    ):
        monkeypatch.delenv("AIEVALS_TEST_KEY", raising=False)
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        with pytest.raises(ProviderError, match="AIEVALS_TEST_KEY"):
            await provider.complete(REQUEST)

    async def test_a_successful_call_is_parsed(self, fake_server: str, monkeypatch):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, _ok("the answer", prompt_tokens=11, completion_tokens=13))]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        result = await provider.complete(REQUEST)
        assert result.text == "the answer"
        assert result.model == "server-model"
        assert (result.prompt_tokens, result.completion_tokens) == (11, 13)
        assert result.latency_ms > 0

    async def test_the_request_carries_the_credential_and_the_messages(
        self, fake_server: str, monkeypatch
    ):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, _ok("x"))]
        provider = OpenAICompatibleProvider(
            {
                "base_url": fake_server,
                "api_key_env": "AIEVALS_TEST_KEY",
                "headers": {"X-Tenant": "acme"},
            }
        )
        await provider.complete(REQUEST)
        seen = FakeHandler.seen[0]
        assert seen["path"].endswith("/chat/completions")
        assert seen["headers"]["Authorization"] == "Bearer test-key-not-a-real-secret"
        assert seen["headers"]["X-Tenant"] == "acme"
        assert seen["body"]["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ]
        assert seen["body"]["stream"] is False

    async def test_a_case_with_no_system_message_sends_one_message(
        self, fake_server: str, monkeypatch
    ):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, _ok("x"))]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        await provider.complete(CompletionRequest(prompt="p", model="m"))
        assert len(FakeHandler.seen[0]["body"]["messages"]) == 1

    async def test_a_rate_limit_is_retried_and_then_succeeds(self, fake_server: str, monkeypatch):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(429, {"error": "slow down"}), (200, _ok("second try"))]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY", "attempts": 3}
        )
        result = await provider.complete(REQUEST)
        assert result.text == "second try"
        assert len(FakeHandler.seen) == 2

    async def test_an_authentication_failure_is_not_retried(self, fake_server: str, monkeypatch):
        # Retrying a 401 three times wastes time and tells nobody anything.
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(401, {"error": "bad key"})]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY", "attempts": 3}
        )
        with pytest.raises(ProviderError, match="HTTP 401"):
            await provider.complete(REQUEST)
        assert len(FakeHandler.seen) == 1

    async def test_retries_are_bounded(self, fake_server: str, monkeypatch):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(503, {}), (503, {}), (503, {})]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY", "attempts": 2}
        )
        with pytest.raises(ProviderError, match="HTTP 503"):
            await provider.complete(REQUEST)
        assert len(FakeHandler.seen) == 2

    async def test_a_non_json_response_is_reported_clearly(self, fake_server: str, monkeypatch):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, "<html>a gateway error page</html>")]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        with pytest.raises(ProviderError, match="not JSON"):
            await provider.complete(REQUEST)

    async def test_a_response_with_no_choices_is_reported(self, fake_server: str, monkeypatch):
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, {"model": "m", "choices": []})]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        with pytest.raises(ProviderError, match="no choices"):
            await provider.complete(REQUEST)

    async def test_missing_usage_leaves_the_counts_unknown_rather_than_zero(
        self, fake_server: str, monkeypatch
    ):
        # Zero would be a measurement, and a token budget silently satisfied by
        # a provider that counts nothing is not a budget.
        monkeypatch.setenv("AIEVALS_TEST_KEY", "test-key-not-a-real-secret")
        FakeHandler.script = [(200, {"model": "m", "choices": [{"message": {"content": "x"}}]})]
        provider = OpenAICompatibleProvider(
            {"base_url": fake_server, "api_key_env": "AIEVALS_TEST_KEY"}
        )
        result = await provider.complete(REQUEST)
        assert result.total_tokens is None

    def test_an_attempt_count_below_one_is_refused(self):
        with pytest.raises(ProviderError, match="at least 1"):
            OpenAICompatibleProvider({"base_url": "https://x/v1", "attempts": 0})

    def test_non_mapping_headers_are_refused(self):
        with pytest.raises(ProviderError, match="mapping"):
            OpenAICompatibleProvider({"base_url": "https://x/v1", "headers": ["a"]})

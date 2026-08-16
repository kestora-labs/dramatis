"""Tests for the Anthropic provider.

Every test here runs against a fake client except the one marked ``live``, which is
deselected by default. The fake asserts the request shape — that is what catches a
parameter current models reject, without spending anything to find out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from dramatis.providers import ModelRequest, ProviderError
from dramatis.providers.anthropic_provider import (
    DEFAULT_MODEL,
    STREAMING_THRESHOLD,
    AnthropicProvider,
)

SCHEMA = {
    "type": "object",
    "properties": {"characters": {"type": "array", "items": {"type": "string"}}},
    "required": ["characters"],
    "additionalProperties": False,
}


@dataclass
class FakeBlock:
    type: str
    text: str


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20


class FakeMessage:
    def __init__(self, text: str = '{"characters": []}', stop_reason: str = "end_turn") -> None:
        self.content = [FakeBlock("text", text)]
        self.model = "claude-opus-5"
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeStream:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_final_message(self) -> FakeMessage:
        return self._message


class FakeMessages:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeMessage:
        self.create_calls.append(kwargs)
        return self._message

    def stream(self, **kwargs: Any) -> FakeStream:
        self.stream_calls.append(kwargs)
        return FakeStream(self._message)


class FakeClient:
    def __init__(self, message: FakeMessage | None = None) -> None:
        self.messages = FakeMessages(message or FakeMessage())


def provider(client: FakeClient | None = None, **kwargs: Any) -> AnthropicProvider:
    return AnthropicProvider(client=client or FakeClient(), **kwargs)


class TestRequestShape:
    def test_sends_prompt_as_a_user_message(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="Who appears here?"))

        sent = client.messages.create_calls[0]
        assert sent["messages"] == [{"role": "user", "content": "Who appears here?"}]

    def test_system_is_a_top_level_field(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", system="You extract characters."))

        assert client.messages.create_calls[0]["system"] == "You extract characters."

    def test_system_is_omitted_when_absent(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p"))

        assert "system" not in client.messages.create_calls[0]

    def test_never_sends_sampling_parameters(self) -> None:
        """Current models reject temperature/top_p/top_k with a 400."""
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", effort="high"))

        sent = client.messages.create_calls[0]
        assert not {"temperature", "top_p", "top_k"} & set(sent)

    def test_effort_and_schema_share_one_container(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", effort="high", output_schema=SCHEMA))

        output_config = client.messages.create_calls[0]["output_config"]
        assert output_config["effort"] == "high"
        assert output_config["format"] == {"type": "json_schema", "schema": SCHEMA}

    def test_output_config_is_omitted_when_nothing_needs_it(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", effort=None))

        assert "output_config" not in client.messages.create_calls[0]

    def test_defaults_to_the_current_model(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p"))

        assert client.messages.create_calls[0]["model"] == DEFAULT_MODEL
        assert DEFAULT_MODEL == "claude-opus-5"

    def test_the_model_is_overridable(self) -> None:
        client = FakeClient()
        provider(client, model="claude-haiku-4-5").complete(ModelRequest(prompt="p"))

        assert client.messages.create_calls[0]["model"] == "claude-haiku-4-5"


class TestStreaming:
    def test_small_requests_do_not_stream(self) -> None:
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", max_tokens=STREAMING_THRESHOLD))

        assert client.messages.create_calls and not client.messages.stream_calls

    def test_large_requests_stream(self) -> None:
        """Above the threshold the SDK refuses a non-streaming call outright."""
        client = FakeClient()
        provider(client).complete(ModelRequest(prompt="p", max_tokens=STREAMING_THRESHOLD + 1))

        assert client.messages.stream_calls and not client.messages.create_calls

    def test_a_streamed_response_reads_the_same(self) -> None:
        client = FakeClient(FakeMessage(text='{"characters": ["Ada"]}'))
        response = provider(client).complete(
            ModelRequest(prompt="p", max_tokens=STREAMING_THRESHOLD + 1)
        )

        assert response.json() == {"characters": ["Ada"]}


class TestResponse:
    def test_reports_the_model_that_served_it(self) -> None:
        response = provider().complete(ModelRequest(prompt="p"))
        assert response.model == "claude-opus-5"
        assert response.provider == "anthropic"

    def test_carries_token_usage(self) -> None:
        response = provider().complete(ModelRequest(prompt="p"))
        assert (response.input_tokens, response.output_tokens) == (100, 20)

    def test_concatenates_text_blocks_and_ignores_others(self) -> None:
        message = FakeMessage()
        message.content = [
            FakeBlock("thinking", "ignored"),
            FakeBlock("text", "one "),
            FakeBlock("text", "two"),
        ]
        response = provider(FakeClient(message)).complete(ModelRequest(prompt="p"))

        assert response.text == "one two"

    def test_a_refusal_surfaces_as_a_flag_not_an_exception(self) -> None:
        message = FakeMessage(text="", stop_reason="refusal")
        response = provider(FakeClient(message)).complete(ModelRequest(prompt="p"))

        assert response.refused is True


class TestCredentials:
    def test_no_key_is_ever_passed_to_the_client(self) -> None:
        """The SDK resolves the credential; accepting one here invites hardcoding."""
        import inspect

        signature = inspect.signature(AnthropicProvider.__init__)
        assert "api_key" not in signature.parameters

    def test_credential_detection_does_not_reveal_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        assert AnthropicProvider.credential_available() is True

        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert AnthropicProvider.credential_available() is False


class TestOptionalDependency:
    def test_importing_dramatis_never_requires_the_sdk(self) -> None:
        """Invariant 6: reading and exporting an analysis must work with no provider SDK."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.modules['anthropic'] = None\n"
                "import dramatis, dramatis.validation, dramatis.store, dramatis.providers\n"
                "print('ok')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_a_missing_sdk_explains_how_to_install_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any):
            if name == "anthropic":
                raise ModuleNotFoundError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

        with pytest.raises(ProviderError, match=r"dramatis\[anthropic\]"):
            AnthropicProvider().complete(ModelRequest(prompt="p"))


@pytest.mark.live
class TestAgainstTheRealProvider:
    """Deselected by default. Run with `pytest -m live` when re-recording cassettes.

    Its job is not coverage — the fakes cover behaviour. It is to catch the provider
    changing its response shape underneath the adapter, which no fake can notice.
    """

    def test_a_real_call_returns_the_shape_the_adapter_expects(self) -> None:
        if not AnthropicProvider.credential_available():
            pytest.skip("no credential in the environment")

        response = AnthropicProvider().complete(
            ModelRequest(
                prompt='Reply with the JSON object {"ok": true} and nothing else.',
                max_tokens=64,
                effort="low",
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            )
        )

        assert not response.refused
        assert response.json() == {"ok": True}
        assert response.model
        assert response.input_tokens and response.input_tokens > 0

"""Analysis that never leaves the machine.

Phase 4's acceptance asks that *a full analysis completes against a local model with the
machine offline*, and `TestAFullAnalysisWithNoNetwork` is that sentence as a test: the whole
pipeline, through a transport that fails if it is ever pointed anywhere but the local host.

The rest is the honesty of the adapter. Ollama has no reasoning-effort dial and no refusal
signal, and `OLLAMA_HOST` can point at another machine. Each of those is somewhere a local
provider could quietly stop being local, or stop doing what a run's parameters say it did.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from dramatis.providers import ModelRequest, ProviderError, ProviderUnavailable
from dramatis.providers.ollama_provider import (
    DEFAULT_HOST,
    MAXIMUM_CONTEXT,
    MINIMUM_CONTEXT,
    OllamaProvider,
    context_for,
    default_host,
)


def a_transport(reply: dict, *, seen: list | None = None):
    """A transport returning one canned reply, recording what it was asked to send.

    The method is recorded because Ollama cares about it, and a fake that ignored the verb
    is what let `available()` ship posting to a GET-only endpoint.
    """

    def send(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
        if seen is not None:
            seen.append(
                {
                    "method": method,
                    "url": url,
                    "body": json.loads(payload) if payload else None,
                    "timeout": timeout,
                }
            )
        return json.dumps(reply).encode("utf-8")

    return send


def a_chat_reply(content: str = '{"ok": true}', **over) -> dict:
    return {
        "model": "llama3.1",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 11,
        "eval_count": 7,
        **over,
    }


def a_request(**over) -> ModelRequest:
    fields = {"prompt": "Ada met Bram at the gate.", "system": "Read this.", "max_tokens": 512}
    fields.update(over)
    return ModelRequest(**fields)


class TestSpeakingOllama:
    def test_it_posts_a_chat_to_the_local_host(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())

        assert seen[0]["url"] == f"{DEFAULT_HOST}/api/chat"

    def test_the_system_prompt_becomes_a_system_message(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())
        roles = [message["role"] for message in seen[0]["body"]["messages"]]

        assert roles == ["system", "user"]

    def test_a_request_with_no_system_prompt_sends_only_the_text(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request(system=None))

        assert [m["role"] for m in seen[0]["body"]["messages"]] == ["user"]

    def test_it_asks_for_one_reply_rather_than_a_stream(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())

        assert seen[0]["body"]["stream"] is False

    def test_the_token_budget_reaches_the_server(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request(max_tokens=1234))

        assert seen[0]["body"]["options"]["num_predict"] == 1234

    def test_a_schema_constrains_the_decoding(self) -> None:
        seen: list = []
        schema = {"type": "object", "required": ["characters"]}
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request(output_schema=schema))

        assert seen[0]["body"]["format"] == schema

    def test_no_schema_sends_no_format(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())

        assert "format" not in seen[0]["body"]

    def test_the_reply_becomes_a_model_response(self) -> None:
        provider = OllamaProvider(transport=a_transport(a_chat_reply('{"a": 1}')))
        response = provider.complete(a_request())

        assert response.text == '{"a": 1}'
        assert response.provider == "ollama"
        assert response.model == "llama3.1"
        assert response.input_tokens == 11
        assert response.output_tokens == 7

    def test_the_model_asked_for_is_the_model_sent(self) -> None:
        seen: list = []
        provider = OllamaProvider(model="qwen3", transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())

        assert seen[0]["body"]["model"] == "qwen3"

    def test_an_unspecified_model_falls_back_rather_than_sending_nothing(self) -> None:
        # Callers forward an optional setting they did not choose; None must not become "".
        assert OllamaProvider(model=None).model == "llama3.1"


class TestTheContextWindowIsAskedForRatherThanAssumed:
    """**F5**: Ollama enforces `num_ctx` by discarding the *head* of the prompt, silently.

    Measured against a real `llama3.2:3b`: a prompt of some 11,600 tokens came back with
    `prompt_eval_count` of 2,050. No error, no warning, HTTP 200. The instruction at the end
    of the prompt survived and the passage it referred to did not — so the model was asked to
    find characters in text it had never seen, and answered plausibly having read nothing.

    That is the worst failure this project can have: not a crash, but a confident answer to a
    question nobody asked, which would be recorded in a snapshot as a reading of the work.
    """

    def test_every_call_states_the_context_it_needs(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(ModelRequest(prompt="Ada met Bram.", max_tokens=64))

        assert "num_ctx" in seen[0]["body"]["options"]

    def test_the_window_grows_with_the_passage(self) -> None:
        # An extraction window is thousands of characters; the default Ollama would have
        # applied is 2,048 tokens for everything.
        small = context_for(ModelRequest(prompt="Ada met Bram.", max_tokens=64))
        large = context_for(ModelRequest(prompt="x" * 60_000, max_tokens=4096))

        assert large > small
        assert large > 8192

    def test_it_never_asks_for_less_than_the_floor(self) -> None:
        # A short prompt must still not land near Ollama's own default, or a slightly longer
        # one next call would silently cross it.
        assert context_for(ModelRequest(prompt="", max_tokens=16)) == MINIMUM_CONTEXT
        assert MINIMUM_CONTEXT > 2048

    def test_it_never_asks_for_more_than_the_ceiling(self) -> None:
        # Context costs memory on the machine the user is sitting at, and a model asked for
        # more than the hardware has fails to load at all.
        assert context_for(ModelRequest(prompt="x" * 5_000_000, max_tokens=4096)) == (
            MAXIMUM_CONTEXT
        )

    def test_the_system_prompt_counts_towards_it(self) -> None:
        # It is sent in the same request and occupies the same window; leaving it out of the
        # estimate is how an under-ask happens on exactly the calls that carry instructions.
        without = context_for(ModelRequest(prompt="x" * 30_000, max_tokens=64))
        with_system = context_for(
            ModelRequest(prompt="x" * 30_000, system="y" * 30_000, max_tokens=64)
        )

        assert with_system > without

    def test_the_estimate_is_pessimistic_about_tokenisation(self) -> None:
        # Three characters per token, not four: a corpus of names, German and markup
        # tokenises worse than English prose, and under-asking is what truncates.
        wanted = context_for(ModelRequest(prompt="x" * 30_000, max_tokens=1000))

        assert wanted >= 30_000 / 4 + 1000

    def test_a_caller_may_overrule_it(self) -> None:
        # A smaller window is a real choice on modest hardware. Making it explicitly is not
        # the same as having a server default make it silently.
        seen: list = []
        provider = OllamaProvider(context=4096, transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(ModelRequest(prompt="x" * 60_000, max_tokens=64))

        assert seen[0]["body"]["options"]["num_ctx"] == 4096


class TestRunningOutOfBudget:
    def test_a_truncated_reply_is_reported_as_truncated(self) -> None:
        """`ModelResponse.truncated` reads `max_tokens`, and Ollama says `length`. Left
        untranslated, a cut-off reply arrives at `response.json()` as "the model emitted
        malformed JSON", sending the reader to the prompt rather than to the budget."""
        provider = OllamaProvider(transport=a_transport(a_chat_reply(done_reason="length")))
        response = provider.complete(a_request())

        assert response.truncated
        assert response.stop_reason == "max_tokens"

    def test_a_finished_reply_is_not_truncated(self) -> None:
        provider = OllamaProvider(transport=a_transport(a_chat_reply()))

        assert not provider.complete(a_request()).truncated

    def test_truncation_is_reported_before_the_json_is_blamed(self) -> None:
        provider = OllamaProvider(
            transport=a_transport(a_chat_reply('{"a":', done_reason="length"))
        )
        response = provider.complete(a_request())

        with pytest.raises(ProviderError, match="output token limit"):
            response.json()

    def test_a_missing_done_reason_is_left_alone(self) -> None:
        reply = a_chat_reply()
        del reply["done_reason"]
        provider = OllamaProvider(transport=a_transport(reply))

        assert provider.complete(a_request()).stop_reason is None

    def test_ollama_never_reports_a_refusal(self) -> None:
        # There is no refusal signal in the API. A local model that declines returns prose,
        # which fails validation as it should, rather than being mistaken for an empty read.
        provider = OllamaProvider(transport=a_transport(a_chat_reply("I would rather not.")))

        assert not provider.complete(a_request()).refused


class TestWhenItCannotAnswer:
    def _raising(self, error: Exception):
        def send(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
            raise error

        return send

    def test_a_server_that_is_not_running_says_how_to_start_it(self) -> None:
        provider = OllamaProvider(
            transport=self._raising(urllib.error.URLError("Connection refused"))
        )

        with pytest.raises(ProviderUnavailable, match="ollama serve"):
            provider.complete(a_request())

    def test_a_missing_model_names_the_command_that_installs_it(self) -> None:
        """The first-run failure, and `ollama pull` is the whole of the fix. A bare 404
        sends somebody to a search engine instead."""
        error = urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/chat", 404, "Not Found", {}, None
        )
        provider = OllamaProvider(model="qwen3", transport=self._raising(error))

        with pytest.raises(ProviderError, match="ollama pull qwen3"):
            provider.complete(a_request())

    def test_a_model_error_in_a_200_body_is_still_an_error(self) -> None:
        provider = OllamaProvider(
            model="qwen3", transport=a_transport({"error": 'model "qwen3" not found'})
        )

        with pytest.raises(ProviderError, match="ollama pull qwen3"):
            provider.complete(a_request())

    def test_a_server_fault_is_retryable_rather_than_a_rejection(self) -> None:
        error = urllib.error.HTTPError("http://x/api/chat", 503, "Unavailable", {}, None)
        provider = OllamaProvider(transport=self._raising(error))

        with pytest.raises(ProviderUnavailable):
            provider.complete(a_request())

    def test_a_slow_machine_is_told_apart_from_a_broken_one(self) -> None:
        provider = OllamaProvider(transport=self._raising(TimeoutError()))

        with pytest.raises(ProviderUnavailable, match="did not answer"):
            provider.complete(a_request())

    def test_a_non_json_reply_is_a_clean_error(self) -> None:
        def send(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
            return b"<html>proxy</html>"

        with pytest.raises(ProviderError, match="not JSON"):
            OllamaProvider(transport=send).complete(a_request())


class TestKnowingWhetherTheServerIsThere:
    """`available()` gates the live test. Wrong, and the live test skips itself forever on
    exactly the machines that could have run it — which is how this shipped, until a real
    Ollama with no models answered 405 to a POST at `/api/tags`."""

    def test_it_asks_the_way_ollama_answers(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport({"models": []}, seen=seen))
        provider.available()

        assert seen[0]["method"] == "GET"
        assert seen[0]["url"].endswith("/api/tags")

    def test_it_reports_which_models_are_installed(self) -> None:
        reply = {"models": [{"model": "llama3.2:3b"}, {"model": "qwen3:4b"}]}

        assert OllamaProvider(transport=a_transport(reply)).models() == ["llama3.2:3b", "qwen3:4b"]

    def test_an_empty_server_reports_no_models_rather_than_failing(self) -> None:
        # "Running and empty" and "not running" are different problems with different fixes.
        assert OllamaProvider(transport=a_transport({"models": []})).models() == []

    def test_a_running_server_with_no_models_is_still_available(self) -> None:
        # The state a freshly installed Ollama is in, and the one that has to read as "yes".
        assert OllamaProvider(transport=a_transport({"models": []})).available()

    def test_a_server_that_is_not_running_is_not_available(self) -> None:
        def refuse(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
            raise urllib.error.URLError("Connection refused")

        assert not OllamaProvider(transport=refuse).available()

    def test_a_chat_is_still_posted(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request())

        assert seen[0]["method"] == "POST"


class TestWhereItSends:
    def test_the_default_host_is_this_machine(self) -> None:
        assert OllamaProvider().is_local

    def test_it_honours_ollama_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.9:11434")

        assert default_host() == "http://192.168.1.9:11434"

    def test_a_bare_host_and_port_still_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ollama itself accepts this spelling; one that failed to parse here would send the
        # two forms to different places.
        monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")

        assert default_host() == "http://127.0.0.1:11434"

    def test_a_remote_host_is_reported_as_not_local(self) -> None:
        """The one promise this provider exists to keep. A remote Ollama is a reasonable
        thing to run, and it means the manuscript leaves the machine; the user is told
        rather than left to infer it from a variable they set months ago."""
        assert not OllamaProvider(host="http://192.168.1.9:11434").is_local

    def test_localhost_by_name_counts_as_local(self) -> None:
        assert OllamaProvider(host="http://localhost:11434").is_local

    def test_an_address_that_is_not_http_is_refused(self) -> None:
        with pytest.raises(ProviderError, match="not an http"):
            OllamaProvider(host="ftp://example.com")

    def test_it_calls_exactly_one_host_and_no_other(self) -> None:
        # Invariant 7. Every request this provider makes goes to the address it was given.
        seen: list = []
        provider = OllamaProvider(
            host="http://127.0.0.1:9999", transport=a_transport(a_chat_reply(), seen=seen)
        )
        provider.complete(a_request())
        provider.available()

        assert {entry["url"].split("/api/")[0] for entry in seen} == {"http://127.0.0.1:9999"}


class TestTheEffortDialIsNotConnected:
    def test_the_provider_says_it_does_not_honour_effort(self) -> None:
        assert OllamaProvider.honours_effort is False

    def test_effort_is_not_smuggled_into_the_request(self) -> None:
        # Inventing a field the server ignores would be worse than omitting it: the run
        # would look configured in a way it was not.
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request(effort="max"))

        assert "effort" not in json.dumps(seen[0]["body"])

    def test_two_efforts_produce_the_same_call(self) -> None:
        seen: list = []
        provider = OllamaProvider(transport=a_transport(a_chat_reply(), seen=seen))
        provider.complete(a_request(effort="low"))
        provider.complete(a_request(effort="max"))

        assert seen[0]["body"] == seen[1]["body"]

    def test_the_command_says_so_rather_than_leaving_it_implied(self, capsys) -> None:
        import argparse

        from dramatis.cli import _provider_for

        args = argparse.Namespace(provider="ollama", model=None, host=None, effort="max")
        _provider_for(args)

        assert "no reasoning-effort setting" in capsys.readouterr().err

    def test_the_command_says_when_a_local_run_is_not_local(self, capsys) -> None:
        import argparse

        from dramatis.cli import _provider_for

        args = argparse.Namespace(
            provider="ollama", model=None, host="http://192.168.1.9:11434", effort=None
        )
        _provider_for(args)

        assert "will leave it" in capsys.readouterr().err

    def test_host_is_refused_for_a_provider_it_cannot_apply_to(self) -> None:
        import argparse

        from dramatis.cli import _provider_for

        args = argparse.Namespace(
            provider="anthropic", model=None, host="http://127.0.0.1:1", effort=None
        )
        with pytest.raises(ProviderError, match="--host applies to"):
            _provider_for(args)


class TestAFullAnalysisWithNoNetwork:
    """Phase 4's acceptance: *a full analysis completes against a local model with the
    machine offline*."""

    def test_it_produces_a_snapshot_without_leaving_the_machine(self, tmp_path: Path) -> None:
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.store import Store

        reached: list[str] = []

        def local_only(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
            reached.append(url)
            if not url.startswith("http://127.0.0.1:"):
                raise AssertionError(f"this analysis left the machine: {url}")

            body = json.loads(payload)
            step = "resolve" if "canonical_name" in json.dumps(body.get("format") or {}) else "read"
            if step == "resolve":
                content = json.dumps(
                    {
                        "groups": [
                            {
                                "canonical_name": name,
                                "forms": [name],
                                "kind": "person",
                                "same_as_registered": "",
                            }
                            for name in ("Ada", "Bram")
                        ]
                    }
                )
            else:
                content = json.dumps(
                    {
                        "characters": [
                            {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram")
                        ],
                        "interactions": [
                            {
                                "participants": ["Ada", "Bram"],
                                "quotation": "Ada met Bram at the gate.",
                                "note": "",
                            }
                        ],
                    }
                )
            return json.dumps(a_chat_reply(content)).encode("utf-8")

        source = tmp_path / "work.txt"
        source.write_text(
            "Ada met Bram at the gate.\n\nBram did not answer her.\n",
            encoding="utf-8",
            newline="",
        )

        with Store(tmp_path / "p.sqlite") as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A Set")
            result = analyse(
                store,
                ingested.revision_id,
                OllamaProvider(transport=local_only),
            )

        assert result.snapshot.id.startswith("snap:")
        assert [c["name"] for c in result.snapshot.document["characters"]] == ["Ada", "Bram"]
        assert len(result.snapshot.document["relations"]) == 1
        assert reached, "the analysis made no model call at all"
        assert all(url.startswith("http://127.0.0.1:") for url in reached)

    def test_the_snapshot_records_which_model_read_it(self, tmp_path: Path) -> None:
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.store import Store

        def send(method: str, url: str, payload: bytes | None, timeout: float) -> bytes:
            body = json.loads(payload or b"{}")
            if "canonical_name" in json.dumps(body.get("format") or {}):
                content = json.dumps({"groups": []})
            else:
                content = json.dumps({"characters": [], "interactions": []})
            return json.dumps(a_chat_reply(content, model="qwen3")).encode("utf-8")

        source = tmp_path / "work.txt"
        source.write_text("Nobody is here.\n", encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A Set")
            result = analyse(
                store, ingested.revision_id, OllamaProvider(model="qwen3", transport=send)
            )
            run = result.snapshot.document["analysis_runs"][0]

        assert run["model"] == "qwen3"
        assert run["provider"] == "ollama"


@pytest.mark.live
class TestAgainstARunningOllama:
    """Deselected by default. Run with `pytest -m live` on a machine that has Ollama.

    Its job is not coverage — the fakes above cover behaviour. It is to catch Ollama
    changing its response shape underneath the adapter, which no fake can notice, and it is
    the only check here that costs nothing but a model download.
    """

    def test_a_real_call_returns_the_shape_the_adapter_expects(self) -> None:
        # Whatever this machine has, rather than a name compiled in: a live test that fails
        # because the developer pulled a different model is a live test people switch off.
        probe = OllamaProvider()
        if not probe.available():
            pytest.skip(f"no Ollama answering at {probe.host}")
        installed = probe.models()
        if not installed:
            pytest.skip("Ollama is running but has no models; try `ollama pull llama3.2:3b`")

        provider = OllamaProvider(model=installed[0])
        response = provider.complete(
            ModelRequest(
                prompt='Reply with the JSON object {"ok": true} and nothing else.',
                max_tokens=64,
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            )
        )

        assert response.json() == {"ok": True}
        assert response.model
        assert response.provider == "ollama"
        assert response.input_tokens and response.input_tokens > 0
        assert response.output_tokens and response.output_tokens > 0
        assert not response.truncated

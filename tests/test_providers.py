"""Tests for the provider layer.

The property that earns this module its complexity is that a stale recording fails
loudly. Everything else here is ordinary plumbing; the fingerprint is the part that stops
the suite from passing against an answer to a question the code no longer asks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dramatis.providers import (
    ModelRequest,
    ModelResponse,
    Provider,
    ProviderError,
    StaleRecordingError,
    describe_run,
)
from dramatis.providers.cassette import (
    CASSETTE_VERSION,
    Cassette,
    CheckpointProvider,
    RecordingProvider,
    ReplayProvider,
)
from dramatis.providers.scripted import ScriptedProvider


def a_request(**overrides: Any) -> ModelRequest:
    base = {
        "prompt": "List the characters.",
        "system": "You extract characters.",
        "max_tokens": 512,
    }
    return ModelRequest(**{**base, **overrides})


# -- the contract ------------------------------------------------------------------------


class TestModelRequest:
    def test_carries_no_sampling_parameters(self) -> None:
        """Current models reject temperature/top_p/top_k; a field for them would be a trap."""
        fields = set(ModelRequest.__dataclass_fields__)
        assert not fields & {"temperature", "top_p", "top_k"}

    def test_fingerprint_is_stable_across_equal_requests(self) -> None:
        assert a_request().fingerprint() == a_request().fingerprint()

    @pytest.mark.parametrize(
        "change",
        [
            {"prompt": "Something else entirely."},
            {"system": "A different instruction."},
            {"max_tokens": 1024},
            {"effort": "high"},
            {"output_schema": {"type": "object"}},
        ],
    )
    def test_every_field_that_changes_the_answer_changes_the_fingerprint(
        self, change: dict[str, Any]
    ) -> None:
        assert a_request().fingerprint() != a_request(**change).fingerprint()

    def test_metadata_does_not_change_the_fingerprint(self) -> None:
        """Metadata labels the call for humans; it never reaches the provider."""
        labelled = a_request(metadata={"step": "extract", "segment": "3"})
        assert labelled.fingerprint() == a_request().fingerprint()

    def test_whitespace_changes_are_a_different_request(self) -> None:
        """A prompt edit is an edit, even a small one — that is the point."""
        assert a_request().fingerprint() != a_request(prompt="List the  characters.").fingerprint()


class TestModelResponse:
    def test_parses_json(self) -> None:
        response = ModelResponse(text='{"characters": []}', model="m", provider="p")
        assert response.json() == {"characters": []}

    def test_non_json_is_a_clean_error_naming_the_model(self) -> None:
        response = ModelResponse(text="I'm afraid I can't", model="m", provider="p")
        with pytest.raises(ProviderError, match="p/m"):
            response.json()

    def test_refusal_is_a_successful_call_with_no_content(self) -> None:
        """A decline is not an exception; code that ignores it sees an empty extraction."""
        refused = ModelResponse(text="", model="m", provider="p", stop_reason="refusal")
        assert refused.refused is True
        assert (
            ModelResponse(text="ok", model="m", provider="p", stop_reason="end_turn").refused
            is False
        )

    def test_run_metadata_records_the_model_that_served_it(self) -> None:
        """Invariant 4: a citable snapshot names what actually ran, not what was asked for."""
        provider = ScriptedProvider(["{}"])
        served = ModelResponse(text="{}", model="served-model", provider="anthropic")

        assert describe_run(provider, served, "extract-v1") == {
            "provider": "anthropic",
            "model": "served-model",
            "prompt_version": "extract-v1",
        }


# -- scripted ----------------------------------------------------------------------------


class TestScriptedProvider:
    def test_satisfies_the_provider_protocol(self) -> None:
        assert isinstance(ScriptedProvider([]), Provider)

    def test_yields_responses_in_order(self) -> None:
        provider = ScriptedProvider(["first", "second"])
        assert provider.complete(a_request()).text == "first"
        assert provider.complete(a_request()).text == "second"

    def test_accepts_dicts_as_json(self) -> None:
        provider = ScriptedProvider([{"characters": ["Ada"]}])
        assert provider.complete(a_request()).json() == {"characters": ["Ada"]}

    def test_running_out_fails_loudly(self) -> None:
        """A silent repeat would let a test pass while the code made unexpected calls."""
        provider = ScriptedProvider(["only one"])
        provider.complete(a_request())
        with pytest.raises(ProviderError, match="ran out of responses"):
            provider.complete(a_request())

    def test_records_the_requests_it_was_given(self) -> None:
        provider = ScriptedProvider(["x", "y"])
        provider.complete(a_request(prompt="one"))
        provider.complete(a_request(prompt="two"))

        assert provider.call_count == 2
        assert [call.prompt for call in provider.calls] == ["one", "two"]

    def test_a_callable_can_answer_dynamically(self) -> None:
        provider = ScriptedProvider(lambda request: f"saw {len(request.prompt)} chars")
        assert "saw" in provider.complete(a_request()).text


# -- cassettes ---------------------------------------------------------------------------


@pytest.fixture
def cassette_path(tmp_path: Path) -> Path:
    return tmp_path / "extract.cassette.json"


class TestRecordAndReplay:
    def test_a_recorded_exchange_replays_identically(self, cassette_path: Path) -> None:
        live = ScriptedProvider([ModelResponse(text='{"ok": true}', model="m-1", provider="stub")])
        request = a_request()

        recorded = RecordingProvider(live, cassette_path).complete(request)
        replayed = ReplayProvider(cassette_path).complete(request)

        assert replayed == recorded
        assert replayed.model == "m-1"

    def test_recording_writes_a_versioned_file(self, cassette_path: Path) -> None:
        RecordingProvider(ScriptedProvider(["x"]), cassette_path).complete(a_request())

        payload = json.loads(cassette_path.read_text(encoding="utf-8"))
        assert payload["cassette_version"] == CASSETTE_VERSION
        assert payload["recorded_at"]
        assert len(payload["interactions"]) == 1

    def test_replay_makes_no_call_to_the_inner_provider(self, cassette_path: Path) -> None:
        live = ScriptedProvider(["recorded"])
        RecordingProvider(live, cassette_path).complete(a_request())
        assert live.call_count == 1

        ReplayProvider(cassette_path).complete(a_request())
        assert live.call_count == 1, "replay reached the live provider"

    def test_several_interactions_coexist(self, cassette_path: Path) -> None:
        live = ScriptedProvider(["one", "two"])
        recorder = RecordingProvider(live, cassette_path)
        recorder.complete(a_request(prompt="first"))
        recorder.complete(a_request(prompt="second"))

        replay = ReplayProvider(cassette_path)
        assert replay.complete(a_request(prompt="second")).text == "two"
        assert replay.complete(a_request(prompt="first")).text == "one"

    def test_re_recording_replaces_the_matching_interaction(self, cassette_path: Path) -> None:
        RecordingProvider(ScriptedProvider(["old"]), cassette_path).complete(a_request())
        RecordingProvider(ScriptedProvider(["new"]), cassette_path).complete(a_request())

        cassette = Cassette.load(cassette_path)
        assert len(cassette) == 1
        assert ReplayProvider(cassette_path).complete(a_request()).text == "new"


class TestStaleRecordingsFailLoudly:
    """The whole reason recordings are usable at all."""

    def test_an_edited_prompt_is_not_silently_served(self, cassette_path: Path) -> None:
        RecordingProvider(ScriptedProvider(["recorded"]), cassette_path).complete(a_request())

        with pytest.raises(StaleRecordingError) as failure:
            ReplayProvider(cassette_path).complete(a_request(prompt="an edited prompt"))

        assert "prompt" in str(failure.value), "the message should name the field that changed"
        assert "stale" in str(failure.value)

    def test_the_message_names_every_differing_field(self, cassette_path: Path) -> None:
        RecordingProvider(ScriptedProvider(["recorded"]), cassette_path).complete(a_request())

        with pytest.raises(StaleRecordingError) as failure:
            ReplayProvider(cassette_path).complete(
                a_request(prompt="different", effort="max", max_tokens=99)
            )

        message = str(failure.value)
        for field in ("prompt", "effort", "max_tokens"):
            assert field in message

    def test_a_missing_cassette_says_how_to_make_one(self, tmp_path: Path) -> None:
        with pytest.raises(StaleRecordingError, match="RecordingProvider"):
            ReplayProvider(tmp_path / "absent.cassette.json")

    def test_an_empty_cassette_says_so(self, cassette_path: Path) -> None:
        Cassette(cassette_path).save()

        with pytest.raises(StaleRecordingError, match="empty"):
            ReplayProvider(cassette_path).complete(a_request())

    def test_an_outdated_cassette_version_is_rejected(self, cassette_path: Path) -> None:
        cassette_path.write_text(
            json.dumps({"cassette_version": 0, "interactions": []}), encoding="utf-8"
        )
        with pytest.raises(StaleRecordingError, match="Re-record"):
            Cassette.load(cassette_path)


class TestCheckpointResumesARun:
    """A run is dozens of calls that are discarded together if any later stage raises.

    The property under test throughout: work already paid for is never paid for twice, and
    work whose determining inputs changed is never served from a stale answer.
    """

    def test_a_recorded_call_is_served_without_reaching_the_provider(
        self, cassette_path: Path
    ) -> None:
        RecordingProvider(ScriptedProvider(["done"]), cassette_path).complete(a_request())

        # Scripted with nothing: reaching it at all raises rather than answering.
        live = ScriptedProvider([])
        resumed = CheckpointProvider(live, cassette_path)

        assert resumed.complete(a_request()).text == "done"
        assert live.call_count == 0

    def test_an_unrecorded_call_reaches_the_provider_and_is_kept(self, cassette_path: Path) -> None:
        live = ScriptedProvider(["fresh"])
        CheckpointProvider(live, cassette_path).complete(a_request())

        assert live.call_count == 1
        assert ReplayProvider(cassette_path).complete(a_request()).text == "fresh"

    def test_it_counts_what_it_served_and_what_it_fetched(self, cassette_path: Path) -> None:
        provider = CheckpointProvider(ScriptedProvider(["one", "two"]), cassette_path)
        provider.complete(a_request(prompt="first"))
        provider.complete(a_request(prompt="second"))
        provider.complete(a_request(prompt="first"))

        assert (provider.fetched, provider.served) == (2, 1)

    def test_a_second_run_pays_only_for_the_calls_the_first_did_not_make(
        self, cassette_path: Path
    ) -> None:
        """The failure this bullet exists for: a run dies partway and is started again."""
        first = ScriptedProvider(["w0", "w1"])
        interrupted = CheckpointProvider(first, cassette_path)
        interrupted.complete(a_request(prompt="window 0"))
        interrupted.complete(a_request(prompt="window 1"))

        second = ScriptedProvider(["w2"])
        resumed = CheckpointProvider(second, cassette_path)
        for prompt in ("window 0", "window 1", "window 2"):
            resumed.complete(a_request(prompt=prompt))

        assert second.call_count == 1, "a completed call was paid for twice"
        assert (resumed.served, resumed.fetched) == (2, 1)

    def test_it_saves_after_each_call_rather_than_at_the_end(self, cassette_path: Path) -> None:
        """A checkpoint written when the run finishes is worthless to the run that did not."""
        provider = CheckpointProvider(ScriptedProvider(["one", "two"]), cassette_path)

        provider.complete(a_request(prompt="first"))
        assert len(Cassette.load(cassette_path)) == 1, "nothing on disk after the first call"

        provider.complete(a_request(prompt="second"))
        assert len(Cassette.load(cassette_path)) == 2

    @pytest.mark.parametrize(
        "change",
        [
            {"prompt": "a different window"},
            {"system": "an edited prompt"},
            {"max_tokens": 32768},
            {"effort": "max"},
            {"output_schema": {"type": "object"}},
        ],
    )
    def test_a_changed_request_is_fetched_afresh(
        self, cassette_path: Path, change: dict[str, Any]
    ) -> None:
        """No invalidation logic of its own — the fingerprint already decides this (D7).

        The case that matters in practice is `max_tokens`: raising one stage's budget must
        re-run that stage and leave every other call served from disk.
        """
        RecordingProvider(ScriptedProvider(["stale"]), cassette_path).complete(a_request())

        live = ScriptedProvider(["current"])
        served = CheckpointProvider(live, cassette_path).complete(a_request(**change))

        assert served.text == "current"
        assert live.call_count == 1

    def test_a_missing_checkpoint_is_started_rather_than_refused(self, tmp_path: Path) -> None:
        """Unlike replay, a first run has nothing to resume and that is not an error."""
        absent = tmp_path / "nested" / "run.checkpoint.json"
        CheckpointProvider(ScriptedProvider(["first"]), absent).complete(a_request())

        assert absent.is_file()

    def test_recording_still_always_calls_live(self, cassette_path: Path) -> None:
        """The two must not converge: a re-record that served its own stale answer is useless."""
        RecordingProvider(ScriptedProvider(["old"]), cassette_path).complete(a_request())

        live = ScriptedProvider(["new"])
        assert RecordingProvider(live, cassette_path).complete(a_request()).text == "new"
        assert live.call_count == 1


class TestCassettesSurviveAnInterruptedWrite:
    def test_saving_leaves_no_debris_beside_the_cassette(self, cassette_path: Path) -> None:
        provider = CheckpointProvider(ScriptedProvider(["one", "two"]), cassette_path)
        provider.complete(a_request(prompt="first"))
        provider.complete(a_request(prompt="second"))

        alongside = {path.name for path in cassette_path.parent.iterdir()}
        assert alongside == {cassette_path.name}

    def test_a_failed_write_leaves_the_previous_cassette_intact(
        self, cassette_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of writing to one side and renaming: an interruption costs the new
        call, never the calls already banked."""
        provider = CheckpointProvider(ScriptedProvider(["one", "two"]), cassette_path)
        provider.complete(a_request(prompt="first"))

        def explode(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("dramatis.providers.cassette.os.replace", explode)
        with pytest.raises(OSError):
            provider.complete(a_request(prompt="second"))

        assert len(Cassette.load(cassette_path)) == 1, "the banked call was lost"


class TestCassettesLeakNothing:
    def test_a_cassette_never_contains_a_credential(
        self, cassette_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cassettes are committed; a key written into one would be published."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-appear")
        RecordingProvider(ScriptedProvider(["x"]), cassette_path).complete(a_request())

        assert "sk-ant-should-never-appear" not in cassette_path.read_text(encoding="utf-8")

    def test_a_cassette_warns_that_it_holds_prompt_text(self, cassette_path: Path) -> None:
        RecordingProvider(ScriptedProvider(["x"]), cassette_path).complete(a_request())

        payload = json.loads(cassette_path.read_text(encoding="utf-8"))
        assert "public-domain or synthetic" in payload["note"]

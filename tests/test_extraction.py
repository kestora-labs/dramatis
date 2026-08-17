"""Tests for the map stage of extraction.

Two properties carry most of the weight. Windows must never split a segment, or a
quotation spanning the join would be unfindable in either half. And the stage must stay
*raw*: names as the page wrote them, no merging, no weighting, no rejection — so that
later stages have something checkable to work from.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from dramatis.extraction import (
    DEFAULT_WINDOW_CHARACTERS,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    ExtractionError,
    build_windows,
    extract,
    prompt_sha256,
    system_prompt,
)
from dramatis.providers import ModelRequest, ModelResponse, ProviderError
from dramatis.providers.scripted import ScriptedProvider
from dramatis.segmentation import SegmentationSpec, SegmentRule, segment_text

FIXTURE_A = Path(__file__).resolve().parents[1] / "fixtures" / "a" / "source"

PASSAGE = 'Ada met Bram at the gate.\n\n"You came," she said.\n\nBram did not answer her.\n'


def _prose(text: str) -> str:
    """Collapse runs of whitespace.

    Prompt paragraphs are single long lines, but assertions about their wording should not
    break the day somebody re-wraps one.
    """
    return re.sub(r"\s+", " ", text)


def a_reply(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"characters": [], "interactions": []}
    base.update(overrides)
    return base


def one_interaction(quotation: str = '"You came," she said.') -> dict[str, Any]:
    return a_reply(
        characters=[
            {"name": "Ada", "aliases": ["she"], "kind": "person"},
            {"name": "Bram", "aliases": [], "kind": "person"},
        ],
        interactions=[
            {
                "participants": ["Ada", "Bram"],
                "quotation": quotation,
                "note": "They meet at the gate.",
            }
        ],
    )


# -- windowing ---------------------------------------------------------------------------


class TestWindows:
    def test_a_short_text_is_one_window(self) -> None:
        windows = build_windows(segment_text(PASSAGE))
        assert len(windows) == 1

    def test_windows_never_split_a_segment(self) -> None:
        """A quotation spanning a split would be findable in neither half."""
        segmentation = segment_text("\n\n".join(f"Block {n} of text." for n in range(40)))
        windows = build_windows(segmentation, target_characters=60)

        covered = [position for window in windows for position in window.segment_positions]
        assert covered == segmentation.leaves(), "every leaf appears exactly once, in order"

        for window in windows:
            for position in window.segment_positions:
                segment = segmentation.segments[position]
                assert window.start <= segment.start and segment.end <= window.end

    def test_windows_tile_the_segmented_text(self) -> None:
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(20)))
        windows = build_windows(segmentation, target_characters=50)

        cursor = segmentation.segments[segmentation.leaves()[0]].start
        for window in windows:
            assert window.start == cursor
            cursor = window.end
        assert cursor == len(segmentation.text)

    def test_a_segment_longer_than_the_budget_still_gets_a_window(self) -> None:
        """The budget is a target, not a ceiling — the alternative is dropping the segment."""
        segmentation = segment_text("x" * 500)
        windows = build_windows(segmentation, target_characters=10)

        assert len(windows) == 1
        assert windows[0].length == 500

    def test_windows_are_numbered_in_order(self) -> None:
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(20)))
        windows = build_windows(segmentation, target_characters=40)

        assert [window.index for window in windows] == list(range(len(windows)))

    def test_a_nonpositive_budget_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            build_windows(segment_text(PASSAGE), target_characters=0)

    def test_default_budget_is_a_sane_size(self) -> None:
        assert 1_000 < DEFAULT_WINDOW_CHARACTERS < 100_000


# -- the request ---------------------------------------------------------------------------


class TestTheRequest:
    def test_one_call_per_window(self) -> None:
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(20)))
        windows = build_windows(segmentation, target_characters=40)
        provider = ScriptedProvider([a_reply()] * len(windows))

        extract(segmentation, provider, target_characters=40)

        assert provider.call_count == len(windows)

    def test_the_prompt_is_the_window_text_and_nothing_else(self) -> None:
        segmentation = segment_text(PASSAGE)
        provider = ScriptedProvider([a_reply()])

        extract(segmentation, provider)

        assert provider.calls[0].prompt == PASSAGE

    def test_a_schema_is_sent_so_the_reply_is_parseable_by_construction(self) -> None:
        provider = ScriptedProvider([a_reply()])
        extract(segment_text(PASSAGE), provider)

        assert provider.calls[0].output_schema == RESPONSE_SCHEMA
        assert RESPONSE_SCHEMA["additionalProperties"] is False

    def test_each_call_is_labelled_with_its_window(self) -> None:
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(10)))
        windows = build_windows(segmentation, target_characters=30)
        provider = ScriptedProvider([a_reply()] * len(windows))

        extract(segmentation, provider, target_characters=30)

        labels = [call.metadata for call in provider.calls]
        assert all(label["step"] == "extract" for label in labels)
        assert [label["window"] for label in labels] == [str(n) for n in range(len(windows))]

    def test_the_system_prompt_demands_verbatim_quotation(self) -> None:
        """Phase 1.7 verifies this; asking for something checkable is what enables checking."""
        assert "exactly, character for character" in system_prompt().replace("\n", " ")
        assert "paraphrase" in system_prompt()

    def test_the_system_prompt_permits_an_empty_answer(self) -> None:
        """Without this, a passage with no characters invites invention."""
        assert "empty lists" in system_prompt()


class TestThePromptIsAFile:
    """D18. The prompt ships as a file so it can be read and revised; the consequence is
    that a run must record which prompt it actually used, not which one it claims."""

    def test_the_default_prompt_is_the_base_file_with_nothing_stripped_or_added(self) -> None:
        """Anything trimmed, templated, or reflowed on the way out would make "the prompt
        that was sent" and "the file on disk" two different strings with one hash."""
        from importlib.resources import files

        on_disk = files("dramatis.prompts").joinpath("extract.md").read_text(encoding="utf-8")

        assert system_prompt() == on_disk

    def test_enabling_collectives_appends_the_fragment_and_nothing_else(self) -> None:
        """Two files rather than two prompts, so the difference between the questions is
        the only thing that differs."""
        from importlib.resources import files

        fragment = (
            files("dramatis.prompts").joinpath("extract-collectives.md").read_text(encoding="utf-8")
        )
        composed = system_prompt(collectives_are_actors=True)

        assert composed.startswith(system_prompt().rstrip())
        assert composed.endswith(fragment)

    def test_the_two_settings_are_different_prompts(self) -> None:
        """The whole of D19's comparability guarantee rests on this being true."""
        assert prompt_sha256(collectives_are_actors=True) != prompt_sha256(
            collectives_are_actors=False
        )

    def test_the_prompt_sent_is_the_prompt_hashed(self) -> None:
        """The hash is worthless if it covers a different string from the one that went."""
        import hashlib

        segmentation = segment_text(PASSAGE)
        provider = ScriptedProvider([a_reply()])
        extraction = extract(segmentation, provider)

        sent = provider.calls[0].system
        assert extraction.prompt_sha256 == hashlib.sha256(sent.encode("utf-8")).hexdigest()

    def test_an_extraction_records_the_prompt_it_used(self) -> None:
        extraction = extract(segment_text(PASSAGE), ScriptedProvider([a_reply()]))

        assert extraction.prompt_sha256 == prompt_sha256()
        assert extraction.prompt_version == PROMPT_VERSION

    def test_a_missing_prompt_says_the_installation_is_broken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It is not a corpus problem or a credential problem, and saying so saves the
        reader from looking where the fault is not."""
        import dramatis.extraction as extraction_module

        extraction_module.read_prompt.cache_clear()
        monkeypatch.setattr(extraction_module, "PROMPT_FILE", "not-a-prompt.md")
        try:
            with pytest.raises(ExtractionError, match="missing from the installation"):
                extraction_module.system_prompt()
        finally:
            extraction_module.read_prompt.cache_clear()


class TestWhatCountsAsACharacter:
    """D19 and the first live run, which made "the Netherfield party" a character standing
    beside Miss Bingley and Mrs Hurst, who were already characters in their own right."""

    def test_the_default_prompt_excludes_groups(self) -> None:
        prompt = _prose(system_prompt())

        assert "Do not report a group as a character" in prompt
        assert "Report instead the people in it that the passage names" in prompt

    def test_the_default_prompt_excludes_indefinite_referents(self) -> None:
        """Not a collective and not governed by the setting: "another young man" is a
        phrase standing in for someone the passage never identifies."""
        prompt = _prose(system_prompt())

        assert "Do not report an indefinite reference as a character" in prompt
        assert "an unidentified someone cannot hold a relationship" in prompt

    def test_indefinite_referents_are_excluded_under_both_settings(self) -> None:
        assert "indefinite reference" in _prose(system_prompt(collectives_are_actors=True))

    def test_enabling_collectives_says_which_instruction_it_replaces(self) -> None:
        """A prompt carrying "do not report a group" followed by "report a group" is a
        contradiction unless the second one says it supersedes the first."""
        composed = _prose(system_prompt(collectives_are_actors=True))

        assert "the instruction above not to report a group is replaced" in composed

    def test_enabling_collectives_still_demands_the_named_members(self) -> None:
        """A group standing in place of its members loses the people, which is worse than
        the double-counting the default avoids."""
        composed = _prose(system_prompt(collectives_are_actors=True))

        assert "a group never stands in place of the people in it" in composed

    def test_the_setting_reaches_the_provider(self) -> None:
        for enabled in (True, False):
            provider = ScriptedProvider([a_reply()])
            extract(segment_text(PASSAGE), provider, collectives_are_actors=enabled)

            sent = _prose(provider.calls[0].system)
            assert ("Report a group as a character when" in sent) is enabled

    def test_the_extraction_records_the_prompt_the_setting_produced(self) -> None:
        provider = ScriptedProvider([a_reply()])
        extraction = extract(segment_text(PASSAGE), provider, collectives_are_actors=True)

        assert extraction.prompt_sha256 == prompt_sha256(collectives_are_actors=True)
        assert extraction.prompt_sha256 != prompt_sha256(collectives_are_actors=False)


class TestWindowIsolation:
    def test_windows_are_read_independently(self) -> None:
        """No window sees another's text, so one window's error cannot propagate."""
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(6)))
        provider = ScriptedProvider([a_reply()] * 6)

        extract(segmentation, provider, target_characters=20)

        prompts = [call.prompt for call in provider.calls]
        assert len(set(prompts)) == len(prompts)


# -- parsing -------------------------------------------------------------------------------


class TestParsing:
    def test_reads_characters_and_interactions(self) -> None:
        result = extract(segment_text(PASSAGE), ScriptedProvider([one_interaction()]))

        assert [character.name for character in result.characters] == ["Ada", "Bram"]
        interaction = result.interactions[0]
        assert interaction.participants == ("Ada", "Bram")
        assert interaction.note == "They meet at the gate."

    def test_keeps_names_as_the_page_wrote_them(self) -> None:
        """Resolution is phase 1.5; normalising here would destroy what it needs."""
        reply = a_reply(
            characters=[
                {"name": "Ada", "aliases": ["Miss Vance"], "kind": "person"},
                {"name": "ada", "aliases": [], "kind": "person"},
            ]
        )
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert [character.name for character in result.characters] == ["Ada", "ada"]

    def test_keeps_the_quotation_byte_for_byte(self) -> None:
        """Normalising here would break the verbatim check in phase 1.7."""
        quotation = '"You  came,"   she said.'
        result = extract(segment_text(PASSAGE), ScriptedProvider([one_interaction(quotation)]))

        assert result.interactions[0].quotation == quotation

    def test_an_alias_equal_to_the_name_is_dropped(self) -> None:
        reply = a_reply(characters=[{"name": "Ada", "aliases": ["Ada", "she"], "kind": "person"}])
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert result.characters[0].aliases == ("she",)

    def test_an_empty_answer_is_valid(self) -> None:
        result = extract(segment_text(PASSAGE), ScriptedProvider([a_reply()]))

        assert result.characters == [] and result.interactions == []

    def test_the_quotation_is_located_to_a_segment(self) -> None:
        result = extract(segment_text(PASSAGE), ScriptedProvider([one_interaction()]))

        position = result.interactions[0].segment_position
        assert position is not None
        assert "You came" in segment_text(PASSAGE).text_of(position)

    def test_a_quotation_that_is_not_in_the_window_is_recorded_not_rejected(self) -> None:
        """Rejection is phase 1.7's decision; recording the miss is what lets it decide."""
        reply = one_interaction("A sentence that appears nowhere in the passage.")
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert len(result.interactions) == 1
        assert result.interactions[0].segment_position is None

    def test_a_line_wrapped_quotation_still_locates(self) -> None:
        text = "Ada spoke to Bram.\n\nIn vain have I\nstruggled. It will not do.\n"
        reply = one_interaction("In vain have I struggled.")
        result = extract(segment_text(text), ScriptedProvider([reply]))

        assert result.interactions[0].segment_position is not None


class TestMalformedReplies:
    def test_an_interaction_without_two_participants_is_dropped_with_a_warning(self) -> None:
        """The schema cannot express 'exactly two', so the code must."""
        reply = a_reply(
            interactions=[
                {"participants": ["Ada"], "quotation": "x", "note": ""},
                {"participants": ["Ada", "Bram", "Cai"], "quotation": "y", "note": ""},
            ]
        )
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert result.interactions == []
        assert len(result.warnings) == 2
        assert "participant" in result.warnings[0]

    def test_a_self_interaction_is_dropped(self) -> None:
        reply = a_reply(
            interactions=[{"participants": ["Ada", "Ada"], "quotation": "x", "note": ""}]
        )
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert result.interactions == []
        assert "self-interaction" in result.warnings[0]

    def test_an_interaction_without_a_quotation_is_dropped(self) -> None:
        reply = a_reply(
            interactions=[{"participants": ["Ada", "Bram"], "quotation": "   ", "note": ""}]
        )
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert result.interactions == []
        assert "no quotation" in result.warnings[0]

    def test_a_nameless_character_is_dropped(self) -> None:
        reply = a_reply(characters=[{"name": "  ", "aliases": [], "kind": "person"}])
        result = extract(segment_text(PASSAGE), ScriptedProvider([reply]))

        assert result.characters == []
        assert "no name" in result.warnings[0]

    def test_non_json_names_the_window(self) -> None:
        provider = ScriptedProvider(["this is not JSON"])
        with pytest.raises(ExtractionError, match="window 0"):
            extract(segment_text(PASSAGE), provider)

    def test_a_json_array_is_rejected(self) -> None:
        provider = ScriptedProvider(["[]"])
        with pytest.raises(ExtractionError, match="expected a JSON object"):
            extract(segment_text(PASSAGE), provider)

    def test_a_refusal_is_not_treated_as_an_empty_window(self) -> None:
        """The most dangerous failure here: a decline silently read as 'no characters'."""
        refusal = ModelResponse(text="", model="m", provider="p", stop_reason="refusal")
        provider = ScriptedProvider([refusal])

        with pytest.raises(ExtractionError, match="declined"):
            extract(segment_text(PASSAGE), provider)

    def test_a_provider_failure_names_the_window(self) -> None:
        def fail(_: ModelRequest) -> ModelResponse:
            raise ProviderError("the provider is unwell")

        with pytest.raises(ExtractionError, match="window 0"):
            extract(segment_text(PASSAGE), ScriptedProvider(fail))


# -- run metadata --------------------------------------------------------------------------


class TestRunMetadata:
    def test_records_the_prompt_version(self) -> None:
        result = extract(segment_text(PASSAGE), ScriptedProvider([a_reply()]))
        assert result.prompt_version == PROMPT_VERSION

    def test_records_the_model_that_served_it(self) -> None:
        response = ModelResponse(
            text='{"characters": [], "interactions": []}',
            model="served-model",
            provider="anthropic",
            input_tokens=100,
            output_tokens=20,
        )
        result = extract(segment_text(PASSAGE), ScriptedProvider([response]))

        assert (result.model, result.provider) == ("served-model", "anthropic")

    def test_totals_token_usage_across_windows(self) -> None:
        segmentation = segment_text("\n\n".join(f"Block {n}." for n in range(4)))
        windows = build_windows(segmentation, target_characters=20)
        responses = [
            ModelResponse(
                text='{"characters": [], "interactions": []}',
                model="m",
                provider="p",
                input_tokens=10,
                output_tokens=3,
            )
            for _ in windows
        ]
        result = extract(segmentation, ScriptedProvider(responses), target_characters=20)

        assert result.input_tokens == 10 * len(windows)
        assert result.output_tokens == 3 * len(windows)

    def test_a_prompt_version_change_is_visible(self) -> None:
        """Two graphs under different prompt versions are not the same analysis."""
        assert PROMPT_VERSION.startswith("extract-v")


# -- against the real fixture --------------------------------------------------------------


@pytest.fixture(scope="module")
def novel() -> str:
    text = (FIXTURE_A / "pride-and-prejudice.txt").read_text(encoding="utf-8")
    return text[text.index("It is a truth universally acknowledged") :]


class TestAgainstTheRealFixture:
    def test_a_real_novel_windows_into_a_workable_number_of_calls(self, novel: str) -> None:
        spec = SegmentationSpec(
            (
                SegmentRule(
                    type="chapter",
                    pattern=re.compile(r"(?i)\bchapter\s+(?P<numeral>[ivxlc]+)\s*\."),
                    label_group="numeral",
                ),
            )
        )
        windows = build_windows(segment_text(novel, spec))

        # Chapters are the leaves here, so windows group whole chapters. A few dozen calls
        # is the shape the pipeline has to be affordable at; hundreds would not be.
        assert 10 < len(windows) < 120

    def test_windows_cover_the_whole_novel(self, novel: str) -> None:
        segmentation = segment_text(novel)
        windows = build_windows(segmentation)

        cursor = segmentation.segments[segmentation.leaves()[0]].start
        for window in windows:
            assert window.start == cursor
            cursor = window.end
        assert cursor == len(novel)

"""Tests for alias resolution.

Three properties carry the weight, and each maps to a way this goes wrong in practice:
identifiers stay stable across runs (or every diff is noise), ambiguous forms are dropped
without a stop-list (or pronouns swallow the graph), and the registry only ever grows (a
merge cannot be reviewed after the fact, so it stays a human act).

The third began as a guard against merging two registered characters. Writing the test
showed the guard could never fire — a form the registry already claims is resolved before
grouping and never reaches it — so the guard was dead code, and the property is asserted
directly instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dramatis import ids
from dramatis.extraction import (
    Extraction,
    MentionedCharacter,
    ObservedInteraction,
    Window,
    WindowFinding,
)
from dramatis.providers import ModelResponse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.resolution import (
    PROMPT_VERSION,
    RESOLUTION_BASE_TOKENS,
    RESOLUTION_MAX_TOKENS,
    RESOLUTION_TOKENS_PER_FORM,
    SYSTEM_PROMPT,
    ResolutionError,
    budget_for,
    resolve,
)
from dramatis.store import AmbiguousAliasError, RegisteredCharacter, Store, form_key

COLLECTION = "col:test"


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "project.sqlite") as opened:
        opened.upsert_collection(COLLECTION, "Test collection")
        yield opened


def window(index: int = 0) -> Window:
    return Window(index=index, start=0, end=10, segment_positions=(index,))


def an_extraction(*windows: list[MentionedCharacter]) -> Extraction:
    findings = tuple(
        WindowFinding(window=window(n), characters=tuple(characters))
        for n, characters in enumerate(windows)
    )
    return Extraction(
        findings=findings, prompt_version="extract-v1", model="m", provider="scripted"
    )


def grouping(*groups: dict[str, Any]) -> str:
    return json.dumps({"groups": list(groups)})


def group(canonical: str, forms: list[str], *, same_as: str = "", kind: str = "person") -> dict:
    return {
        "canonical_name": canonical,
        "forms": forms,
        "kind": kind,
        "same_as_registered": same_as,
    }


# -- the baseline --------------------------------------------------------------------------


class TestWithoutAModel:
    def test_every_distinct_name_becomes_a_character(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter("Ada"), MentionedCharacter("Bram")],
        )
        result = resolve(extraction, store, COLLECTION)

        assert len(result.created) == 2
        assert {c.name for c in store.list_characters(COLLECTION)} == {"Ada", "Bram"}

    def test_it_under_merges_rather_than_guessing(self, store: Store) -> None:
        """The honest floor: never merges what a reader would, never merges what they wouldn't."""
        extraction = an_extraction(
            [MentionedCharacter("Elizabeth Bennet")], [MentionedCharacter("Elizabeth")]
        )
        result = resolve(extraction, store, COLLECTION)

        assert len(result.created) == 2
        assert result.prompt_version is None, "no model ran, so no prompt version applies"

    def test_the_same_name_in_two_windows_is_one_character(self, store: Store) -> None:
        extraction = an_extraction([MentionedCharacter("Ada")], [MentionedCharacter("Ada")])
        result = resolve(extraction, store, COLLECTION)

        assert len(result.created) == 1

    def test_case_and_spacing_are_not_meaningful_distinctions(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter("Ada Vance")], [MentionedCharacter("ada  vance")]
        )
        result = resolve(extraction, store, COLLECTION)

        assert len(result.created) == 1

    def test_an_empty_extraction_resolves_to_nothing(self, store: Store) -> None:
        result = resolve(an_extraction(), store, COLLECTION)
        assert result.assignments == {} and result.created == ()


# -- the token budget ------------------------------------------------------------------------


class TestTheBudgetIsSizedFromTheCast:
    """Resolution is one call that must name every form it was given, so what it costs is
    set by the size of the cast — not by any passage, and not by a constant.

    The fixed 4096 this replaces fit a three-chapter excerpt with twenty-three names and
    truncated on the whole novel, taking sixty-three good extraction calls with it.
    """

    def test_it_grows_with_the_number_of_forms(self) -> None:
        assert budget_for(200) > budget_for(20) > budget_for(0)

    def test_a_cast_of_none_still_affords_an_envelope(self) -> None:
        assert budget_for(0) == RESOLUTION_BASE_TOKENS

    def test_it_allows_for_every_form_being_its_own_group(self) -> None:
        """The worst case is a model that merges nothing — the baseline's own behaviour."""
        forms = 300
        headroom = budget_for(forms) - RESOLUTION_BASE_TOKENS
        assert headroom >= forms * RESOLUTION_TOKENS_PER_FORM

    def test_it_stops_at_a_ceiling(self) -> None:
        """Past this the answer is to batch the names, not to ask for a bigger reply (D22)."""
        assert budget_for(1_000_000) == RESOLUTION_MAX_TOKENS

    def test_the_ceiling_is_within_what_current_models_will_emit(self) -> None:
        assert RESOLUTION_MAX_TOKENS <= 64_000, "smallest current output cap"

    def test_a_negative_count_is_a_programming_error(self) -> None:
        with pytest.raises(ValueError):
            budget_for(-1)

    def test_a_novel_sized_cast_is_afforded_more_than_the_old_constant(self) -> None:
        """The regression this bullet exists for, stated as a number."""
        assert budget_for(250) > 4096

    def test_resolve_asks_for_a_budget_matching_the_forms_it_sends(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter(name) for name in ("Ada", "Bram", "Cleo", "Dara")]
        )
        provider = ScriptedProvider([grouping(group("Ada", ["Ada", "Bram", "Cleo", "Dara"]))])

        resolve(extraction, store, COLLECTION, provider=provider)

        assert provider.calls[0].max_tokens == budget_for(4)

    def test_an_explicit_budget_still_wins(self, store: Store) -> None:
        """Kept so a caller — or a test — can pin the number deliberately."""
        extraction = an_extraction([MentionedCharacter("Ada")])
        provider = ScriptedProvider([grouping(group("Ada", ["Ada"]))])

        resolve(extraction, store, COLLECTION, provider=provider, max_tokens=1234)

        assert provider.calls[0].max_tokens == 1234

    def test_names_the_registry_already_knows_do_not_inflate_the_budget(self, store: Store) -> None:
        """Only unresolved forms reach the model, so only they should be paid for."""
        store.upsert_character(
            RegisteredCharacter(id="char:ada", collection_id=COLLECTION, name="Ada", kind="person")
        )
        extraction = an_extraction([MentionedCharacter("Ada"), MentionedCharacter("Bram")])
        provider = ScriptedProvider([grouping(group("Bram", ["Bram"]))])

        resolve(extraction, store, COLLECTION, provider=provider)

        assert provider.calls[0].max_tokens == budget_for(1)


class TestATruncatedGroupingIsReportedAsSuch:
    def test_it_names_the_budget_rather_than_the_json(self, store: Store) -> None:
        """What the first full-novel run should have said, and did not."""
        extraction = an_extraction([MentionedCharacter("Ada")])
        cut_off = ScriptedProvider(
            [
                ModelResponse(
                    text='{"groups":[{"canonical_name":"Ada","forms":["A',
                    model="m",
                    provider="scripted",
                    stop_reason="max_tokens",
                )
            ]
        )

        with pytest.raises(ResolutionError, match="output token limit"):
            resolve(extraction, store, COLLECTION, provider=cut_off)


# -- ambiguity -------------------------------------------------------------------------------


class TestAmbiguousForms:
    def test_a_form_claimed_by_two_characters_is_dropped(self, store: Store) -> None:
        """How pronouns are excluded without a stop-list — by conflict, not vocabulary."""
        extraction = an_extraction(
            [MentionedCharacter("Ada", aliases=("she",))],
            [MentionedCharacter("Neve", aliases=("she",))],
        )
        result = resolve(extraction, store, COLLECTION)

        assert form_key("she") in result.dropped_forms
        assert result.character_for("she") is None
        assert any("ambiguous" in warning for warning in result.warnings)

    def test_the_warning_names_both_claimants(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter("Ada", aliases=("the girl",))],
            [MentionedCharacter("Neve", aliases=("the girl",))],
        )
        result = resolve(extraction, store, COLLECTION)

        warning = next(w for w in result.warnings if "ambiguous" in w)
        assert "Ada" in warning and "Neve" in warning

    def test_an_unambiguous_alias_is_kept(self, store: Store) -> None:
        extraction = an_extraction([MentionedCharacter("Ada Vance", aliases=("Ada",))])
        result = resolve(extraction, store, COLLECTION)

        assert result.character_for("Ada") == result.character_for("Ada Vance")

    def test_a_form_that_is_another_characters_name_is_not_an_alias(self, store: Store) -> None:
        """Otherwise a passing reference would quietly absorb a character who exists."""
        extraction = an_extraction(
            [MentionedCharacter("Ada", aliases=("Bram",))],
            [MentionedCharacter("Bram")],
        )
        result = resolve(extraction, store, COLLECTION)

        assert result.character_for("Bram") != result.character_for("Ada")
        assert any("own name" in warning for warning in result.warnings)

    def test_no_stop_list_exists_anywhere(self) -> None:
        """A list of pronouns would encode one language's conventions into the core."""
        source = Path("src/dramatis/resolution.py").read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]  # skip the module docstring, which discusses them
        for word in ("'she'", '"she"', "'her'", '"they"'):
            assert word not in body


# -- with a model ----------------------------------------------------------------------------


class TestWithAModel:
    def test_forms_the_model_groups_become_one_character(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter("Elizabeth Bennet")],
            [MentionedCharacter("Elizabeth")],
            [MentionedCharacter("Lizzy")],
        )
        provider = ScriptedProvider(
            [grouping(group("Elizabeth Bennet", ["Elizabeth Bennet", "Elizabeth", "Lizzy"]))]
        )
        result = resolve(extraction, store, COLLECTION, provider=provider)

        assert len(result.created) == 1
        identifier = result.character_for("Lizzy")
        assert (
            identifier
            == result.character_for("Elizabeth")
            == result.character_for("Elizabeth Bennet")
        )

        character = store.get_character(identifier)
        assert character is not None
        assert character.name == "Elizabeth Bennet"
        assert set(character.aliases) == {"Elizabeth", "Lizzy"}

    def test_the_alias_trap(self, store: Store) -> None:
        """Fixture A's central claim: 'Miss Bennet' is Jane, not Elizabeth.

        A pipeline that folds it into Elizabeth produces a graph that looks entirely
        reasonable and is wrong throughout, so this is asserted directly.
        """
        extraction = an_extraction(
            [MentionedCharacter("Elizabeth Bennet", aliases=("Lizzy",))],
            [MentionedCharacter("Jane Bennet", aliases=("Miss Bennet",))],
        )
        provider = ScriptedProvider(
            [
                grouping(
                    group("Elizabeth Bennet", ["Elizabeth Bennet"]),
                    group("Jane Bennet", ["Jane Bennet"]),
                )
            ]
        )
        result = resolve(extraction, store, COLLECTION, provider=provider)

        jane = result.character_for("Jane Bennet")
        assert result.character_for("Miss Bennet") == jane
        assert result.character_for("Lizzy") == result.character_for("Elizabeth Bennet")
        assert result.character_for("Miss Bennet") != result.character_for("Elizabeth Bennet")

    def test_the_prompt_warns_against_merging_on_shared_surnames(self) -> None:
        assert "shorter name is not automatically the same character" in SYSTEM_PROMPT
        assert "related, allied, married" in SYSTEM_PROMPT

    def test_the_prompt_permits_a_group_of_one(self) -> None:
        assert "group of one" in SYSTEM_PROMPT

    def test_the_model_sees_occurrence_counts(self, store: Store) -> None:
        extraction = an_extraction(
            [MentionedCharacter("Ada")], [MentionedCharacter("Ada")], [MentionedCharacter("Bram")]
        )
        provider = ScriptedProvider([grouping(group("Ada", ["Ada"]), group("Bram", ["Bram"]))])
        resolve(extraction, store, COLLECTION, provider=provider)

        prompt = provider.calls[0].prompt
        assert "Ada (seen in 2 passage(s))" in prompt
        assert "Bram (seen in 1 passage(s))" in prompt

    def test_run_metadata_is_recorded(self, store: Store) -> None:
        extraction = an_extraction([MentionedCharacter("Ada")])
        response = ModelResponse(
            text=grouping(group("Ada", ["Ada"])), model="served", provider="anthropic"
        )
        result = resolve(extraction, store, COLLECTION, provider=ScriptedProvider([response]))

        assert result.prompt_version == PROMPT_VERSION
        assert (result.model, result.provider) == ("served", "anthropic")

    def test_a_refusal_is_not_an_empty_grouping(self, store: Store) -> None:
        refusal = ModelResponse(text="", model="m", provider="p", stop_reason="refusal")
        with pytest.raises(ResolutionError, match="declined"):
            resolve(
                an_extraction([MentionedCharacter("Ada")]),
                store,
                COLLECTION,
                provider=ScriptedProvider([refusal]),
            )

    def test_a_malformed_reply_is_a_clean_error(self, store: Store) -> None:
        with pytest.raises(ResolutionError, match="list of groups"):
            resolve(
                an_extraction([MentionedCharacter("Ada")]),
                store,
                COLLECTION,
                provider=ScriptedProvider(['{"not_groups": []}']),
            )


# -- stability across runs --------------------------------------------------------------------


class TestStabilityAcrossRuns:
    def test_a_known_form_keeps_its_identifier(self, store: Store) -> None:
        """The registry, not the naming function, is what makes identity stable."""
        first = resolve(
            an_extraction([MentionedCharacter("Elizabeth Bennet")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Elizabeth Bennet", ["Elizabeth Bennet"]))]),
        )
        original = first.character_for("Elizabeth Bennet")

        # A second run whose model would prefer a different canonical name.
        second = resolve(
            an_extraction([MentionedCharacter("Elizabeth Bennet")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Eliza Bennet", ["Elizabeth Bennet"]))]),
        )

        assert second.character_for("Elizabeth Bennet") == original
        assert second.created == (), "a known form must not create a second character"

    def test_a_new_form_attaches_to_a_registered_character(self, store: Store) -> None:
        resolve(
            an_extraction([MentionedCharacter("Elizabeth Bennet")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Elizabeth Bennet", ["Elizabeth Bennet"]))]),
        )
        result = resolve(
            an_extraction([MentionedCharacter("Lizzy")]),
            store,
            COLLECTION,
            provider=ScriptedProvider(
                [grouping(group("Lizzy", ["Lizzy"], same_as="Elizabeth Bennet"))]
            ),
        )

        assert result.created == ()
        assert result.character_for("Lizzy") == result.character_for("Elizabeth Bennet")

    def test_claiming_an_unknown_registered_name_warns_and_creates(self, store: Store) -> None:
        result = resolve(
            an_extraction([MentionedCharacter("Lizzy")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Lizzy", ["Lizzy"], same_as="Nobody"))]),
        )

        assert len(result.created) == 1
        assert any("not in the registry" in warning for warning in result.warnings)

    def test_identifiers_are_derived_from_the_canonical_name(self, store: Store) -> None:
        result = resolve(
            an_extraction([MentionedCharacter("Elizabeth Bennet")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Elizabeth Bennet", ["Elizabeth Bennet"]))]),
        )
        assert result.character_for("Elizabeth Bennet") == ids.character_id("Elizabeth Bennet")


# -- merges are never automatic -----------------------------------------------------------------


class TestTheRegistryOnlyGrows:
    """Merging is not merely guarded against — it is structurally impossible here.

    A form the registry already claims resolves directly and never reaches the grouping
    stage, so no group can ever gather two existing characters. Writing a guard for it
    produced dead code; asserting the property is what actually holds the line.
    """

    def test_a_model_that_groups_two_registered_characters_changes_nothing(
        self, store: Store
    ) -> None:
        resolve(
            an_extraction([MentionedCharacter("Ada Vance"), MentionedCharacter("A. Vance")]),
            store,
            COLLECTION,
        )
        before = {c.id: c.name for c in store.list_characters(COLLECTION)}
        assert len(before) == 2

        result = resolve(
            an_extraction([MentionedCharacter("Ada Vance"), MentionedCharacter("A. Vance")]),
            store,
            COLLECTION,
            provider=ScriptedProvider([grouping(group("Ada Vance", ["Ada Vance", "A. Vance"]))]),
        )

        after = {c.id: c.name for c in store.list_characters(COLLECTION)}
        assert after == before, "an existing character was merged, renamed, or removed"
        assert result.created == ()

    def test_the_model_is_not_even_consulted_when_everything_is_known(self, store: Store) -> None:
        """No unresolved form means no call — cheaper, and nothing to second-guess."""
        resolve(an_extraction([MentionedCharacter("Ada")]), store, COLLECTION)

        provider = ScriptedProvider([grouping(group("Ada", ["Ada"]))])
        resolve(an_extraction([MentionedCharacter("Ada")]), store, COLLECTION, provider=provider)

        assert provider.call_count == 0

    def test_a_character_count_never_decreases_across_runs(self, store: Store) -> None:
        counts = []
        for names in (["Ada"], ["Ada", "Bram"], ["Bram", "Cai"], ["Ada"]):
            resolve(an_extraction([MentionedCharacter(name) for name in names]), store, COLLECTION)
            counts.append(len(store.list_characters(COLLECTION)))

        assert counts == sorted(counts), f"the registry shrank: {counts}"
        assert counts[-1] == 3


# -- the registry ---------------------------------------------------------------------------------


class TestRegistry:
    def test_a_surface_form_cannot_denote_two_characters(self, store: Store) -> None:
        """Enforced by the primary key, so an ambiguous alias is a write failure."""
        store.upsert_character(
            RegisteredCharacter(id="char:a", collection_id=COLLECTION, name="Ada", aliases=("A.",))
        )
        with pytest.raises(AmbiguousAliasError, match="already claimed"):
            store.upsert_character(
                RegisteredCharacter(
                    id="char:b", collection_id=COLLECTION, name="Bram", aliases=("A.",)
                )
            )

    def test_a_character_can_be_updated_in_place(self, store: Store) -> None:
        store.upsert_character(
            RegisteredCharacter(id="char:a", collection_id=COLLECTION, name="Ada")
        )
        store.upsert_character(
            RegisteredCharacter(
                id="char:a", collection_id=COLLECTION, name="Ada Vance", aliases=("Ada",)
            )
        )

        character = store.get_character("char:a")
        assert character is not None
        assert character.name == "Ada Vance"
        assert character.aliases == ("Ada",)

    def test_lookup_is_by_surface_form(self, store: Store) -> None:
        store.upsert_character(
            RegisteredCharacter(
                id="char:a", collection_id=COLLECTION, name="Ada Vance", aliases=("Lizzy",)
            )
        )

        for form in ("Ada Vance", "ada vance", "  Ada   Vance ", "Lizzy"):
            found = store.find_character_by_form(COLLECTION, form)
            assert found is not None and found.id == "char:a", form

    def test_an_unknown_form_resolves_to_nothing(self, store: Store) -> None:
        assert store.find_character_by_form(COLLECTION, "Nobody") is None

    def test_the_registry_survives_reopening(self, tmp_path: Path) -> None:
        path = tmp_path / "project.sqlite"
        with Store(path) as store:
            store.upsert_collection(COLLECTION, "Test")
            resolve(an_extraction([MentionedCharacter("Ada")]), store, COLLECTION)

        with Store(path) as reopened:
            assert [c.name for c in reopened.list_characters(COLLECTION)] == ["Ada"]

    def test_the_registry_is_scoped_to_a_collection(self, store: Store) -> None:
        """Characters cross works, so the registry sits above the work, not inside it."""
        store.upsert_collection("col:other", "Another collection")
        store.upsert_character(
            RegisteredCharacter(id="char:a", collection_id=COLLECTION, name="Ada")
        )
        store.upsert_character(
            RegisteredCharacter(id="char:a2", collection_id="col:other", name="Ada")
        )

        assert len(store.list_characters(COLLECTION)) == 1
        assert store.find_character_by_form("col:other", "Ada").id == "char:a2"


class TestInteractionsAreNotResolvedHere:
    def test_resolution_does_not_touch_interactions(self, store: Store) -> None:
        """Rewriting interaction participants is phase 1.6's job, not this one's."""
        finding = WindowFinding(
            window=window(),
            characters=(MentionedCharacter("Ada"),),
            interactions=(
                ObservedInteraction(participants=("Ada", "Bram"), quotation="They met."),
            ),
        )
        extraction = Extraction(
            findings=(finding,), prompt_version="extract-v1", model="m", provider="p"
        )
        resolve(extraction, store, COLLECTION)

        assert extraction.interactions[0].participants == ("Ada", "Bram")

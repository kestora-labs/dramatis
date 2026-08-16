"""Tests for aggregation.

The two properties that matter: an interaction that cannot be rewritten onto resolved
characters is dropped rather than attached to a plausible-looking one, and a weight never
travels without the basis it was computed on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dramatis import ids
from dramatis.aggregation import (
    INTERACTION_PASSAGES,
    Aggregation,
    ComparabilityError,
    aggregate,
    require_comparable,
)
from dramatis.extraction import (
    Extraction,
    MentionedCharacter,
    ObservedInteraction,
    Window,
    WindowFinding,
)
from dramatis.providers.scripted import ScriptedProvider
from dramatis.resolution import Resolution, resolve
from dramatis.segmentation import segment_text
from dramatis.store import Store, form_key
from dramatis.validation import validate_document
from tests.documents import minimal_document

COLLECTION = "col:test"

PASSAGE = (
    "Ada met Bram at the gate.\n\nBram did not answer her.\n\nLater, Cai spoke to Ada alone.\n"
)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "project.sqlite") as opened:
        opened.upsert_collection(COLLECTION, "Test collection")
        yield opened


@pytest.fixture
def segmentation():
    return segment_text(PASSAGE)


def interaction(
    first: str, second: str, quotation: str, *, position: int | None = 0, note: str | None = None
) -> ObservedInteraction:
    return ObservedInteraction(
        participants=(first, second),
        quotation=quotation,
        note=note,
        segment_position=position,
    )


RESOLVED = {"Ada": "char:ada", "Bram": "char:bram", "Cai": "char:cai"}
SIMPLE = Resolution(assignments={form_key(name): cid for name, cid in RESOLVED.items()})


# -- grouping ------------------------------------------------------------------------------


class TestGrouping:
    def test_one_relation_per_pair(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada", "Cai", "Later, Cai spoke to Ada alone.", position=2),
            ],
            SIMPLE,
            segmentation,
        )

        assert len(result) == 2
        assert result.relation_between("char:ada", "char:bram") is not None
        assert result.relation_between("char:ada", "char:cai") is not None

    def test_order_of_participants_does_not_create_two_edges(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Bram", "Ada", "Bram did not answer her.", position=1),
            ],
            SIMPLE,
            segmentation,
        )

        assert len(result) == 1
        assert result.relations[0].weight == 2

    def test_endpoints_and_identifier_are_sorted(self, segmentation) -> None:
        """An undirected edge whose identity depended on naming order would defeat diffing."""
        result = aggregate(
            [interaction("Bram", "Ada", "Bram did not answer her.", position=1)],
            SIMPLE,
            segmentation,
        )
        relation = result.relations[0]

        assert (relation.source, relation.target) == ("char:ada", "char:bram")
        assert relation.id == ids.relation_id("char:bram", "char:ada")
        assert relation.id == "rel:ada--bram"

    def test_relations_are_ordered_heaviest_first(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada", "Bram", "Bram did not answer her.", position=1),
                interaction("Ada", "Cai", "Later, Cai spoke to Ada alone.", position=2),
            ],
            SIMPLE,
            segmentation,
        )

        assert [r.weight for r in result.relations] == [2, 1]
        assert result.heaviest().endpoints == frozenset({"char:ada", "char:bram"})

    def test_relations_are_undirected_by_default(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.")], SIMPLE, segmentation
        )
        assert result.relations[0].directed is False


# -- weights -------------------------------------------------------------------------------


class TestWeights:
    def test_weight_counts_distinct_passages(self, segmentation) -> None:
        """Not reported interactions: one exchange described three ways counts once."""
        result = aggregate(
            [
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada", "Bram", "Bram did not answer her.", position=1),
            ],
            SIMPLE,
            segmentation,
        )

        assert result.relations[0].weight == 2

    def test_an_unlocated_interaction_counts_once_on_its_own(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada", "Bram", "A quotation from nowhere.", position=None),
            ],
            SIMPLE,
            segmentation,
        )

        assert result.relations[0].weight == 2

    def test_two_identical_unlocated_quotations_count_once(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Same words.", position=None),
                interaction("Ada", "Bram", "Same  words.", position=None),
            ],
            SIMPLE,
            segmentation,
        )

        assert result.relations[0].weight == 1

    def test_weight_never_travels_without_its_basis(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.")], SIMPLE, segmentation
        )

        assert result.weight_basis == INTERACTION_PASSAGES
        assert all(r.weight_basis == INTERACTION_PASSAGES for r in result.relations)

    def test_the_basis_is_recorded_when_overridden(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.")],
            SIMPLE,
            segmentation,
            weight_basis="something_else",
        )
        assert result.relations[0].weight_basis == "something_else"


class TestComparability:
    def test_matching_bases_compare(self) -> None:
        assert require_comparable(Aggregation(), Aggregation()) == INTERACTION_PASSAGES

    def test_differing_bases_are_an_error(self) -> None:
        """Weights on different bases are different quantities wearing the same name."""
        with pytest.raises(ComparabilityError, match="not comparable"):
            require_comparable(Aggregation(), Aggregation(weight_basis="words_exchanged"))

    def test_the_error_names_both_bases(self) -> None:
        with pytest.raises(ComparabilityError) as failure:
            require_comparable(Aggregation(), Aggregation(weight_basis="words_exchanged"))

        assert INTERACTION_PASSAGES in str(failure.value)
        assert "words_exchanged" in str(failure.value)

    def test_comparable_with_is_available_without_raising(self) -> None:
        assert Aggregation().comparable_with(Aggregation()) is True
        assert Aggregation().comparable_with(Aggregation(weight_basis="other")) is False

    def test_no_aggregations_yields_the_default(self) -> None:
        assert require_comparable() == INTERACTION_PASSAGES


# -- what gets dropped -----------------------------------------------------------------------


class TestDropping:
    def test_an_unresolved_participant_drops_the_interaction(self, segmentation) -> None:
        """Attaching it to a plausible character would be confidently wrong where nobody looks."""
        result = aggregate(
            [interaction("Ada", "she", "Ada met Bram at the gate.")], SIMPLE, segmentation
        )

        assert len(result) == 0
        assert result.dropped == 1
        assert "'she'" in result.warnings[0]

    def test_a_pair_that_resolves_to_one_character_is_dropped(self, segmentation) -> None:
        """Two forms resolution decided are one person: the model saw a relation with nobody."""
        resolution = Resolution(
            assignments={form_key("Elizabeth"): "char:eb", form_key("Lizzy"): "char:eb"}
        )
        result = aggregate(
            [interaction("Elizabeth", "Lizzy", "Ada met Bram at the gate.")],
            resolution,
            segmentation,
        )

        assert len(result) == 0 and result.dropped == 1
        assert "no pair" in result.warnings[0]

    def test_dropping_one_interaction_does_not_lose_the_others(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "she", "Ada met Bram at the gate."),
                interaction("Ada", "Bram", "Bram did not answer her.", position=1),
            ],
            SIMPLE,
            segmentation,
        )

        assert len(result) == 1 and result.dropped == 1

    def test_nothing_at_all_aggregates_cleanly(self, segmentation) -> None:
        result = aggregate([], SIMPLE, segmentation)
        assert len(result) == 0 and result.warnings == ()


# -- evidence ---------------------------------------------------------------------------------


class TestEvidence:
    def test_evidence_carries_the_quotation_and_a_locator(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0)],
            SIMPLE,
            segmentation,
            document_id="doc:1",
        )
        piece = result.relations[0].evidence[0]

        assert piece.quotation == "Ada met Bram at the gate."
        assert piece.locator["document_id"] == "doc:1"
        assert piece.locator["path"][0]["type"] == "section"

    def test_context_surrounds_the_quotation(self, segmentation) -> None:
        text = "Before the line. In vain have I struggled. After the line.\n"
        result = aggregate(
            [interaction("Ada", "Bram", "In vain have I struggled.", position=0)],
            SIMPLE,
            segment_text(text),
        )
        piece = result.relations[0].evidence[0]

        assert piece.prefix.endswith("Before the line.")
        assert piece.suffix.startswith("After the line.")

    def test_an_unlocated_quotation_gets_no_context(self, segmentation) -> None:
        """Inventing context for a quotation we could not find would make re-anchoring lie."""
        result = aggregate(
            [interaction("Ada", "Bram", "Nowhere in the text.", position=None)],
            SIMPLE,
            segmentation,
        )
        piece = result.relations[0].evidence[0]

        assert piece.prefix == "" and piece.suffix == ""

    def test_the_note_is_carried_through(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.", note="They meet.")],
            SIMPLE,
            segmentation,
        )
        assert result.relations[0].evidence[0].note == "They meet."

    def test_evidence_is_ordered_by_position(self, segmentation) -> None:
        result = aggregate(
            [
                interaction("Ada", "Bram", "Later, Cai spoke to Ada alone.", position=2),
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
            ],
            SIMPLE,
            segmentation,
        )
        quotations = [piece.quotation for piece in result.relations[0].evidence]

        assert quotations[0] == "Ada met Bram at the gate."


class TestSchemaShape:
    def test_a_relation_renders_into_a_valid_document(self, segmentation) -> None:
        """The whole point of the aggregation is to become a snapshot, so check it can."""
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0)],
            SIMPLE,
            segmentation,
            document_id="doc:1",
        )

        document = minimal_document()
        document["characters"] = [
            {"id": "char:ada", "name": "Ada", "provenance": "observed"},
            {"id": "char:bram", "name": "Bram", "provenance": "observed"},
        ]
        document["relations"] = [result.relations[0].as_schema()]

        assert validate_document(document) == []

    def test_the_rendered_relation_declares_its_basis(self, segmentation) -> None:
        result = aggregate(
            [interaction("Ada", "Bram", "Ada met Bram at the gate.")], SIMPLE, segmentation
        )
        assert result.relations[0].as_schema()["weight_basis"] == INTERACTION_PASSAGES


# -- end to end ---------------------------------------------------------------------------------


class TestThroughTheWholePipeline:
    def test_extraction_through_resolution_to_relations(self, store: Store) -> None:
        """The first point at which the three stages produce a graph together."""
        segmentation = segment_text(PASSAGE)
        finding = WindowFinding(
            window=Window(index=0, start=0, end=len(PASSAGE), segment_positions=(0, 1, 2)),
            characters=(
                MentionedCharacter("Ada Vance", aliases=("Ada",)),
                MentionedCharacter("Bram"),
            ),
            interactions=(
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Ada Vance", "Bram", "Bram did not answer her.", position=1),
            ),
        )
        extraction = Extraction(
            findings=(finding,), prompt_version="extract-v1", model="m", provider="scripted"
        )

        resolution = resolve(extraction, store, COLLECTION)
        result = aggregate(extraction.interactions, resolution, segmentation)

        # "Ada" and "Ada Vance" are one character, so both interactions land on one edge.
        assert len(result) == 1
        relation = result.relations[0]
        assert relation.weight == 2
        assert result.dropped == 0

    def test_an_alias_dropped_as_ambiguous_costs_its_interactions(self, store: Store) -> None:
        """The cost of refusing to guess, made visible rather than hidden."""
        segmentation = segment_text(PASSAGE)
        finding = WindowFinding(
            window=Window(index=0, start=0, end=len(PASSAGE), segment_positions=(0, 1, 2)),
            characters=(
                MentionedCharacter("Ada", aliases=("she",)),
                MentionedCharacter("Cai", aliases=("she",)),
            ),
            interactions=(interaction("she", "Cai", "Later, Cai spoke to Ada alone.", position=2),),
        )
        extraction = Extraction(
            findings=(finding,), prompt_version="extract-v1", model="m", provider="scripted"
        )

        resolution = resolve(extraction, store, COLLECTION)
        result = aggregate(extraction.interactions, resolution, segmentation)

        assert len(result) == 0
        assert result.dropped == 1
        assert any("resolved to no character" in warning for warning in result.warnings)

    def test_the_pipeline_is_deterministic(self, store: Store) -> None:
        """Phase 3 cannot diff two snapshots if the same input yields different graphs."""
        segmentation = segment_text(PASSAGE)
        finding = WindowFinding(
            window=Window(index=0, start=0, end=len(PASSAGE), segment_positions=(0, 1, 2)),
            characters=(MentionedCharacter("Ada"), MentionedCharacter("Bram")),
            interactions=(
                interaction("Ada", "Bram", "Ada met Bram at the gate.", position=0),
                interaction("Bram", "Ada", "Bram did not answer her.", position=1),
            ),
        )
        extraction = Extraction(
            findings=(finding,), prompt_version="extract-v1", model="m", provider="scripted"
        )
        provider = ScriptedProvider([])

        first = aggregate(
            extraction.interactions, resolve(extraction, store, COLLECTION), segmentation
        )
        second = aggregate(
            extraction.interactions, resolve(extraction, store, COLLECTION), segmentation
        )

        assert [r.as_schema() for r in first.relations] == [r.as_schema() for r in second.relations]
        assert provider.call_count == 0

"""Comparing two snapshots.

Fixture **B** states what a correct diff of its two drafts must say, and says why it matters
more than the changes themselves: *"Both drafts must be analysed by the same run
configuration, or the diff cannot distinguish a rewrite from a better prompt."*
"""

from __future__ import annotations

from typing import Any

import pytest

from dramatis.diff import (
    ADDED,
    ANALYSIS,
    BOTH,
    MERGED,
    REMOVED,
    RETYPED,
    SPLIT,
    STRENGTHENED,
    TEXT,
    WEAKENED,
    DiffError,
    diff_snapshots,
)


def a_snapshot(
    *,
    snapshot: str = "snap:1",
    revision: str = "rev:1",
    run: str = "run:1",
    characters: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    work: str = "work:1",
    prompt: str = "extract-v2",
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "collection": {"id": "col:1", "name": "C"},
        "works": [{"id": work, "title": "A Work"}],
        "snapshot": {"id": snapshot, "text_revision_id": revision, "analysis_run_id": run},
        "analysis_runs": [{"id": run, "model": "m", "prompt_version": prompt}],
        "characters": characters
        if characters is not None
        else [
            {"id": "char:auber", "name": "Auber Vance", "provenance": "observed"},
            {"id": "char:idris", "name": "Idris Kell", "provenance": "observed"},
        ],
        "relations": relations
        if relations is not None
        else [a_relation("char:auber", "char:idris", 10)],
    }


def a_relation(source: str, target: str, weight: float, types: list[str] | None = None):
    relation: dict[str, Any] = {
        "id": f"rel:{source.removeprefix('char:')}--{target.removeprefix('char:')}",
        "source": source,
        "target": target,
        "weight": weight,
        "weight_basis": "interaction_passages",
        "provenance": "observed",
    }
    if types is not None:
        relation["types"] = types
    return relation


def a_character(identifier: str, name: str, aliases: list[str] | None = None):
    character: dict[str, Any] = {"id": identifier, "name": name, "provenance": "observed"}
    if aliases is not None:
        character["aliases"] = aliases
    return character


class TestAttribution:
    """What the fixture says matters as much as the changes."""

    def test_only_the_text_changing_is_credited_to_the_work(self) -> None:
        result = diff_snapshots(
            a_snapshot(revision="rev:1", run="run:1"),
            a_snapshot(snapshot="snap:2", revision="rev:2", run="run:1"),
        )
        assert result.attribution == TEXT

    def test_only_the_analysis_changing_is_credited_to_the_reading(self) -> None:
        # A different *configuration*, not merely a different run identifier: two executions
        # of one configuration are one reading, which is what the next class covers.
        result = diff_snapshots(
            a_snapshot(revision="rev:1", run="run:1", prompt="extract-v2"),
            a_snapshot(snapshot="snap:2", revision="rev:1", run="run:2", prompt="extract-v3"),
        )
        assert result.attribution == ANALYSIS

    def test_both_changing_credits_neither_and_says_so(self) -> None:
        # Picking whichever moved more would be inventing an attribution the evidence does
        # not support, which is the failure Invariant 4 exists to prevent.
        result = diff_snapshots(
            a_snapshot(revision="rev:1", run="run:1", prompt="extract-v2"),
            a_snapshot(snapshot="snap:2", revision="rev:2", run="run:2", prompt="extract-v3"),
        )

        assert result.attribution == BOTH
        assert any("credited to either" in warning for warning in result.warnings)

    def test_two_snapshots_of_one_pairing_report_no_axis_moving(self) -> None:
        result = diff_snapshots(a_snapshot(), a_snapshot(snapshot="snap:2"))
        assert result.attribution == "same"


class TestRelations:
    def test_a_heavier_edge_is_strengthened(self) -> None:
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 10)]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 25)],
            ),
        )
        change = result.relations_of(STRENGTHENED)[0]

        assert change.weight_before == 10
        assert change.weight_after == 25
        assert change.delta == 15

    def test_a_lighter_edge_is_weakened(self) -> None:
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 25)]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 4)],
            ),
        )

        assert result.relations_of(WEAKENED)[0].delta == -21
        assert result.relations_of(STRENGTHENED) == ()

    def test_an_edge_only_in_the_later_snapshot_is_added(self) -> None:
        result = diff_snapshots(
            a_snapshot(relations=[]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 3)],
            ),
        )

        assert [change.kinds for change in result.relations] == [(ADDED,)]
        assert result.relations_of(ADDED)[0].weight_after == 3

    def test_an_edge_only_in_the_earlier_snapshot_is_removed(self) -> None:
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 3)]),
            a_snapshot(snapshot="snap:2", revision="rev:2", relations=[]),
        )

        assert result.relations_of(REMOVED)[0].weight_before == 3

    def test_changed_types_are_a_retyping(self) -> None:
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 10, ["antagonism"])]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 10, ["kinship"])],
            ),
        )
        change = result.relations_of(RETYPED)[0]

        assert change.types_before == ("antagonism",)
        assert change.types_after == ("kinship",)

    def test_reordered_types_are_not_a_retyping(self) -> None:
        # The schema does not order them, so a different order is the same claim.
        result = diff_snapshots(
            a_snapshot(
                relations=[a_relation("char:auber", "char:idris", 10, ["kinship", "antagonism"])]
            ),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 10, ["antagonism", "kinship"])],
            ),
        )

        assert result.relations == ()

    def test_one_edge_that_both_strengthens_and_retypes_is_reported_once(self) -> None:
        # Two entries for one edge would double-count a single change.
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 10, ["antagonism"])]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                relations=[a_relation("char:auber", "char:idris", 30, ["kinship"])],
            ),
        )

        assert len(result.relations) == 1
        assert set(result.relations[0].kinds) == {STRENGTHENED, RETYPED}

    def test_an_unchanged_edge_is_not_reported(self) -> None:
        result = diff_snapshots(a_snapshot(), a_snapshot(snapshot="snap:2", revision="rev:2"))
        assert result.relations == ()
        assert result.empty


class TestWeightsAreComparedOnlyWithinASharedBasis:
    """A weight is a number on a named scale. Two snapshots weighed differently have no
    common scale for 'stronger' to mean anything on."""

    def _mixed(self):
        later = a_relation("char:auber", "char:idris", 99)
        later["weight_basis"] = "words_exchanged"
        return diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 10)]),
            a_snapshot(snapshot="snap:2", revision="rev:2", relations=[later]),
        )

    def test_nothing_is_strengthened_or_weakened_across_bases(self) -> None:
        result = self._mixed()

        assert result.weights_comparable is False
        assert result.relations_of(STRENGTHENED) == ()
        assert result.relations_of(WEAKENED) == ()

    def test_the_refusal_is_explained(self) -> None:
        assert any("not comparable" in warning for warning in self._mixed().warnings)

    def test_a_retyping_is_still_reported_across_bases(self) -> None:
        # Types do not live on the weight scale, so the refusal does not reach them.
        later = a_relation("char:auber", "char:idris", 99, ["kinship"])
        later["weight_basis"] = "words_exchanged"
        result = diff_snapshots(
            a_snapshot(relations=[a_relation("char:auber", "char:idris", 10, ["antagonism"])]),
            a_snapshot(snapshot="snap:2", revision="rev:2", relations=[later]),
        )

        assert result.relations_of(RETYPED)
        assert result.weight_basis is None

    def test_the_basis_is_named_when_it_does_hold(self) -> None:
        result = diff_snapshots(a_snapshot(), a_snapshot(snapshot="snap:2", revision="rev:2"))
        assert result.weight_basis == "interaction_passages"


class TestCharacters:
    def test_a_new_character_is_added(self) -> None:
        result = diff_snapshots(
            a_snapshot(),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[
                    a_character("char:auber", "Auber Vance"),
                    a_character("char:idris", "Idris Kell"),
                    a_character("char:neve", "Neve Vance"),
                ],
                relations=[],
            ),
        )
        change = result.characters_of(ADDED)[0]

        assert change.id == "char:neve"
        assert change.name == "Neve Vance"

    def test_a_departed_character_is_removed(self) -> None:
        result = diff_snapshots(
            a_snapshot(),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[a_character("char:auber", "Auber Vance")],
                relations=[],
            ),
        )

        assert [change.id for change in result.characters_of(REMOVED)] == ["char:idris"]

    def test_a_character_absorbed_into_another_is_a_merge(self) -> None:
        # Recognised because the survivor now answers to the absorbed one's name, which is
        # what the registry writes down when it merges two.
        result = diff_snapshots(
            a_snapshot(
                characters=[
                    a_character("char:auber", "Auber Vance"),
                    a_character("char:auber-v", "Auber V."),
                ],
                relations=[],
            ),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[a_character("char:auber", "Auber Vance", ["Auber V."])],
                relations=[],
            ),
        )
        change = result.characters_of(MERGED)[0]

        assert change.id == "char:auber-v"
        assert change.counterparts == ("char:auber",)
        assert result.characters_of(REMOVED) == ()

    def test_a_character_that_becomes_two_is_a_split(self) -> None:
        result = diff_snapshots(
            a_snapshot(
                characters=[a_character("char:vance", "Auber Vance", ["Neve Vance"])],
                relations=[],
            ),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[
                    a_character("char:vance", "Auber Vance"),
                    a_character("char:neve", "Neve Vance"),
                ],
                relations=[],
            ),
        )
        change = result.characters_of(SPLIT)[0]

        assert change.id == "char:neve"
        assert change.counterparts == ("char:vance",)

    def test_a_rename_is_not_called_a_split(self) -> None:
        # The old character is gone, so there is no one for the new one to have split from.
        # Calling it a split would invent a second person.
        result = diff_snapshots(
            a_snapshot(characters=[a_character("char:old", "Auber Vance")], relations=[]),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[a_character("char:new", "Auber Vance")],
                relations=[],
            ),
        )

        assert result.characters_of(SPLIT) == ()
        assert result.characters_of(MERGED)[0].counterparts == ("char:new",)

    def test_names_are_matched_without_regard_to_case(self) -> None:
        result = diff_snapshots(
            a_snapshot(
                characters=[
                    a_character("char:auber", "Auber Vance"),
                    a_character("char:auber-v", "AUBER V."),
                ],
                relations=[],
            ),
            a_snapshot(
                snapshot="snap:2",
                revision="rev:2",
                characters=[a_character("char:auber", "Auber Vance", ["auber v."])],
                relations=[],
            ),
        )

        assert result.characters_of(MERGED)


class TestAMergeDoesNotFloodTheDiff:
    """Every relation touching an absorbed character would otherwise read as removed and a
    matching one as added — a pile of spurious changes describing one act of curation."""

    def _merged(self, later_weight: float = 10):
        before = a_snapshot(
            characters=[
                a_character("char:auber", "Auber Vance"),
                a_character("char:auber-v", "Auber V."),
                a_character("char:idris", "Idris Kell"),
            ],
            relations=[a_relation("char:auber-v", "char:idris", 10)],
        )
        after = a_snapshot(
            snapshot="snap:2",
            revision="rev:2",
            characters=[
                a_character("char:auber", "Auber Vance", ["Auber V."]),
                a_character("char:idris", "Idris Kell"),
            ],
            relations=[a_relation("char:auber", "char:idris", later_weight)],
        )
        return diff_snapshots(before, after)

    def test_the_edge_is_not_reported_as_removed_and_added(self) -> None:
        result = self._merged()

        assert result.relations_of(ADDED) == ()
        assert result.relations_of(REMOVED) == ()

    def test_a_real_weight_change_across_the_merge_is_still_seen(self) -> None:
        assert self._merged(40).relations_of(STRENGTHENED)[0].delta == 30

    def test_the_merge_itself_is_still_reported(self) -> None:
        assert self._merged().characters_of(MERGED)[0].id == "char:auber-v"


class TestRefusals:
    def test_two_different_works_cannot_be_diffed(self) -> None:
        # Every node and edge would be reported as added or removed; the result would be a
        # list of everything rather than a diff.
        with pytest.raises(DiffError, match="different works"):
            diff_snapshots(a_snapshot(work="work:1"), a_snapshot(work="work:2"))

    def test_a_snapshot_with_nothing_in_it_is_still_comparable(self) -> None:
        result = diff_snapshots(
            a_snapshot(characters=[], relations=[]),
            a_snapshot(snapshot="snap:2", revision="rev:2", characters=[], relations=[]),
        )

        assert result.empty
        assert result.attribution == TEXT


class TestTheAnalysisAxisIsComparedByConfiguration:
    """A run identifier includes when it ran (D33). Comparing identifiers would call two
    executions of one configuration two different analyses, and then credit every change to
    nothing at all."""

    def _with_run(self, run_id: str, *, effort: str = "medium", **kwargs):
        document = a_snapshot(run=run_id, **kwargs)
        document["analysis_runs"] = [
            {
                "id": run_id,
                "model": "m",
                "prompt_version": "extract-v2",
                "parameters": {"effort": effort},
            }
        ]
        return document

    def test_two_runs_of_one_configuration_are_one_analysis(self) -> None:
        result = diff_snapshots(
            self._with_run("run:monday", revision="rev:1"),
            self._with_run("run:tuesday", snapshot="snap:2", revision="rev:2"),
        )

        assert result.attribution == TEXT
        assert not result.warnings or all("credited" not in w for w in result.warnings)

    def test_a_different_configuration_is_a_different_analysis(self) -> None:
        result = diff_snapshots(
            self._with_run("run:1", effort="medium", revision="rev:1"),
            self._with_run("run:2", effort="low", snapshot="snap:2", revision="rev:1"),
        )

        assert result.attribution == ANALYSIS

    def test_it_falls_back_to_the_identifier_when_the_run_is_not_carried(self) -> None:
        # Too strict is better than too generous: an attribution nobody can check is worse
        # than one that refuses. The schema does not require the run to be in the document.
        before = a_snapshot(run="run:1", revision="rev:1")
        after = a_snapshot(run="run:2", snapshot="snap:2", revision="rev:1")
        before["analysis_runs"] = []
        after["analysis_runs"] = []

        assert diff_snapshots(before, after).attribution == ANALYSIS

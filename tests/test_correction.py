"""Human corrections that outlive the reading they were made on (5.2).

The bullet has two halves and both are tested here. *Corrections persist across re-analysis*:
a correction made against snapshot n is written into snapshot n+1 as it is built. *Never
silently overwritten*: where the new reading proposes something else the correction still
stands, and the disagreement is recorded rather than swallowed.

Nothing here touches a network. The model is scripted throughout; where a reading has to be
made to change its mind, the registry it writes into is changed directly, because that is the
part of it a snapshot actually renders.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dramatis import correction, review
from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Store

PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"


def a_reply(kind: str = "person") -> str:
    return json.dumps(
        {
            "characters": [
                {"name": n, "aliases": [], "kind": kind} for n in ("Ada", "Bram", "Cai")
            ],
            "interactions": [
                {
                    "participants": ["Ada", "Bram"],
                    "quotation": "Ada met Bram at the gate.",
                    "note": "",
                },
                {
                    "participants": ["Ada", "Cai"],
                    "quotation": "Cai spoke to Ada alone.",
                    "note": "",
                },
            ],
        }
    )


def a_grouping() -> str:
    return json.dumps(
        {
            "groups": [
                {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                for n in ("Ada", "Bram", "Cai")
            ]
        }
    )


@pytest.fixture
def project(tmp_path: Path):
    """An open store holding one analysed work, its snapshot, and the revision behind it."""
    source = tmp_path / "work.txt"
    source.write_text(PASSAGE, encoding="utf-8", newline="")

    with Store(tmp_path / "project.sqlite") as store:
        ingested = ingest_file(store, source, work_title="A Work", collection_name="A Collection")
        result = analyse(
            store,
            ingested.revision_id,
            ScriptedProvider([a_reply(), a_grouping()]),
            now="2026-01-01T09:00:00+00:00",
        )
        yield store, result.snapshot, ingested.revision_id


def reanalyse(store, revision_id, *, reply=None, at="2026-02-02T09:00:00+00:00"):
    """A second reading of the same text, so a correction can be asked to survive one."""
    return analyse(
        store,
        revision_id,
        ScriptedProvider([reply or a_reply(), a_grouping()]),
        now=at,
    )


def a_character(snapshot) -> str:
    return str(snapshot.document["characters"][0]["id"])


def a_relation(snapshot) -> str:
    return str(snapshot.document["relations"][0]["id"])


def entry_for(document, kind: str, identifier: str):
    key = "characters" if kind == "character" else "relations"
    return next(entry for entry in document[key] if entry["id"] == identifier)


class TestWhatMayBeCorrected:
    def test_a_character_takes_the_fields_a_person_can_judge(self) -> None:
        assert correction.fields_for("character") == ("name", "kind", "aliases", "notes")

    def test_a_relation_takes_its_own(self) -> None:
        assert correction.fields_for("relation") == ("types", "valence", "directed", "notes")

    def test_a_weight_is_refused_because_it_is_a_count_and_not_an_opinion(self) -> None:
        with pytest.raises(correction.CorrectionError, match="count on a declared basis"):
            correction.check_field("relation", "weight")

    def test_evidence_is_refused_because_invariant_3_verifies_it(self) -> None:
        with pytest.raises(correction.CorrectionError, match="Invariant 3"):
            correction.check_field("relation", "evidence")

    def test_identity_is_refused_and_points_at_the_bullet_that_owns_it(self) -> None:
        for name in ("id", "source", "target"):
            with pytest.raises(correction.CorrectionError, match="5.3"):
                correction.check_field("relation", name)

    def test_a_field_of_the_other_kind_says_which_kind_it_belongs_to(self) -> None:
        with pytest.raises(correction.CorrectionError, match="field of a relation"):
            correction.check_field("character", "valence")

    def test_an_unknown_field_lists_what_is_available(self) -> None:
        with pytest.raises(correction.CorrectionError, match="aliases"):
            correction.check_field("character", "hair_colour")


class TestValues:
    def test_a_name_must_not_be_blank(self) -> None:
        with pytest.raises(correction.CorrectionError):
            correction.normalise("character", "name", "   ")

    def test_a_kind_must_be_one_the_schema_knows(self) -> None:
        with pytest.raises(correction.CorrectionError, match="not a character kind"):
            correction.normalise("character", "kind", "protagonist")

    def test_a_valence_outside_the_scale_is_refused(self) -> None:
        with pytest.raises(correction.CorrectionError, match="outside"):
            correction.normalise("relation", "valence", 4)

    def test_a_string_is_not_a_list_of_aliases(self) -> None:
        # "Lizzy" would otherwise be stored as five one-letter aliases.
        with pytest.raises(correction.CorrectionError):
            correction.normalise("character", "aliases", "Lizzy")

    def test_blank_entries_are_dropped_from_a_list(self) -> None:
        assert correction.normalise("character", "aliases", ["Lizzy", "  ", "Eliza"]) == [
            "Lizzy",
            "Eliza",
        ]

    def test_a_whole_number_valence_is_stored_as_a_number(self) -> None:
        assert correction.normalise("relation", "valence", 1) == 1.0


class TestRecording:
    def test_it_keeps_what_the_reading_said(self, project) -> None:
        """`was` is what makes a later disagreement noticeable, so it is captured here."""
        store, snapshot, _ = project
        identifier = a_character(snapshot)
        before = entry_for(snapshot.document, "character", identifier)["name"]

        recorded = correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        assert recorded.was == before
        assert recorded.value == "Ada Mbeki"
        assert recorded.snapshot_id == snapshot.id

    def test_it_also_marks_the_subject_corrected(self, project) -> None:
        """The two are one act: 5.1's status and 5.2's replacement."""
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        entry = review.overlay(store, snapshot).entry_for("character", identifier)
        assert entry is not None
        assert entry.status == "corrected"

    def test_a_recorded_correction_lets_5_1_accept_corrected_without_a_note(self, project) -> None:
        """5.1 refused an unexplained `corrected`. A correction is the explanation, in more
        detail than a sentence would be, so the rule is satisfied rather than waived."""
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        with pytest.raises(review.ReviewError):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind="character",
                subject_id=identifier,
                status=review.CORRECTED,
            )

        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="notes",
            value="the housekeeper, not the daughter",
        )

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            status=review.CORRECTED,
        )

    def test_restating_the_standing_correction_writes_nothing(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        for _ in range(3):
            correction.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind="character",
                subject_id=identifier,
                field="name",
                value="Ada Mbeki",
            )

        assert len(correction.history(store, snapshot.work_id, "character", identifier)) == 1

    def test_the_newest_correction_wins_and_the_earlier_one_survives(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        for name in ("Ada Mbeki", "Ada M. Mbeki"):
            correction.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind="character",
                subject_id=identifier,
                field="name",
                value=name,
            )

        past = correction.history(store, snapshot.work_id, "character", identifier)
        assert [entry.value for entry in past] == ["Ada Mbeki", "Ada M. Mbeki"]
        standing = store.current_corrections(snapshot.work_id)
        assert standing[("character", identifier, "name")].value == "Ada M. Mbeki"

    def test_two_fields_of_one_subject_are_two_corrections(self, project) -> None:
        """Correcting a name and correcting a note are two decisions; one row per subject
        would let the second silently discard the first."""
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )
        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="kind",
            value="entity",
        )

        standing = store.current_corrections(snapshot.work_id)
        assert standing[("character", identifier, "name")].value == "Ada Mbeki"
        assert standing[("character", identifier, "kind")].value == "entity"

    def test_a_subject_the_reading_never_proposed_is_refused(self, project) -> None:
        store, snapshot, _ = project
        with pytest.raises(correction.CorrectionError, match="nothing there to correct"):
            correction.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind="character",
                subject_id="char:nobody",
                field="name",
                value="Nobody",
            )

    def test_the_snapshot_it_was_made_on_is_untouched(self, project) -> None:
        store, snapshot, _ = project
        before = store.get_snapshot(snapshot.id)
        assert before is not None

        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=a_character(snapshot),
            field="name",
            value="Ada Mbeki",
        )

        after = store.get_snapshot(snapshot.id)
        assert after is not None
        assert after.sha256 == before.sha256


class TestSurvivingReanalysis:
    """The acceptance sentence: a correction made in snapshot n survives into n+1 and is
    reported as `human` provenance."""

    def test_a_corrected_name_is_in_the_next_snapshot(self, project) -> None:
        store, first, revision_id = project
        identifier = a_character(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        second = reanalyse(store, revision_id).snapshot

        assert second.id != first.id
        assert entry_for(second.document, "character", identifier)["name"] == "Ada Mbeki"
        # And the first still says what the first said.
        assert entry_for(first.document, "character", identifier)["name"] == "Ada"

    def test_the_corrected_entry_is_reported_as_human(self, project) -> None:
        store, first, revision_id = project
        identifier = a_character(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        second = reanalyse(store, revision_id).snapshot
        entry = entry_for(second.document, "character", identifier)

        assert entry["provenance"] == "human"
        assert entry["review_status"] == "corrected"

    def test_an_edge_survives_the_same_way(self, project) -> None:
        store, first, revision_id = project
        identifier = a_relation(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="relation",
            subject_id=identifier,
            field="types",
            value=["kinship", "estrangement"],
        )

        second = reanalyse(store, revision_id).snapshot
        entry = entry_for(second.document, "relation", identifier)

        assert entry["types"] == ["kinship", "estrangement"]
        assert entry["provenance"] == "human"

    def test_it_survives_a_third_reading_too(self, project) -> None:
        """Applied from the log every time, not carried by the previous snapshot: a
        correction that only survived one hop would fail on the run after next."""
        store, first, revision_id = project
        identifier = a_character(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        reanalyse(store, revision_id)
        third = reanalyse(store, revision_id, at="2026-03-03T09:00:00+00:00").snapshot

        assert entry_for(third.document, "character", identifier)["name"] == "Ada Mbeki"

    def test_an_emptied_note_removes_the_field_rather_than_blanking_it(self, project) -> None:
        # The schema reads an absent field as "the run never said", which is what clearing a
        # note means. A blank string would be a note that says nothing.
        store, first, revision_id = project
        identifier = a_relation(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="relation",
            subject_id=identifier,
            field="notes",
            value="",
        )

        second = reanalyse(store, revision_id).snapshot
        assert "notes" not in entry_for(second.document, "relation", identifier)

    def test_the_corrected_snapshot_still_satisfies_the_schema(self, project) -> None:
        from dramatis.validation import validate_document

        store, first, revision_id = project
        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=a_character(first),
            field="kind",
            value="collective",
        )

        second = reanalyse(store, revision_id).snapshot
        assert validate_document(second.document) == []


class TestNeverSilentlyOverwritten:
    """Where a later reading proposes something other than what a correction replaced.

    A character's fields are written by resolution into the collection registry, and the
    snapshot renders what the registry holds — so *changing its mind* means the registry
    holding something else on the next run. These tests change it directly, which is exactly
    what a later resolution does and is the only part of that machinery under test here.
    """

    def _registry_says(self, store, snapshot, **changes):
        collection_id = str(snapshot.document["collection"]["id"])
        character = store.get_character(a_character(snapshot))
        assert character is not None
        store.upsert_character(replace(character, collection_id=collection_id, **changes))

    def test_a_reading_that_disagrees_is_overruled_and_recorded(self, project) -> None:
        store, first, revision_id = project
        identifier = a_character(first)

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=identifier,
            field="kind",
            value="entity",
        )
        self._registry_says(store, first, kind="collective")

        result = reanalyse(store, revision_id)

        assert entry_for(result.snapshot.document, "character", identifier)["kind"] == "entity"
        conflicts = store.list_correction_conflicts(first.work_id, snapshot_id=result.snapshot.id)
        assert [(c.field, c.proposed, c.held) for c in conflicts] == [
            ("kind", "collective", "entity")
        ]

    def test_the_disagreement_is_reported_to_whoever_ran_it(self, project) -> None:
        store, first, revision_id = project
        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=a_character(first),
            field="kind",
            value="entity",
        )
        self._registry_says(store, first, kind="collective")

        result = reanalyse(store, revision_id)

        assert any("The correction stands" in warning for warning in result.warnings)

    def test_a_reading_that_still_agrees_raises_nothing(self, project) -> None:
        """Only a *changed* proposal is a disagreement. Reporting every applied correction as
        a conflict would make the report worthless by the tenth one."""
        store, first, revision_id = project
        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=a_character(first),
            field="kind",
            value="entity",
        )

        result = reanalyse(store, revision_id)

        assert result.corrections.conflicts == ()
        assert store.list_correction_conflicts(first.work_id) == []

    def test_a_correction_whose_subject_is_gone_is_reported_not_resurrected(self, project) -> None:
        """Putting the character back would invent a node with no evidence behind it. What is
        owed is telling somebody their work has nothing left to attach to."""
        store, first, revision_id = project
        identifier = "char:cai"

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Cai Reiner",
        )

        without_cai = json.dumps(
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
        grouping = json.dumps(
            {
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ]
            }
        )
        result = analyse(
            store,
            revision_id,
            ScriptedProvider([without_cai, grouping]),
            now="2026-04-04T09:00:00+00:00",
        )

        assert identifier not in {c["id"] for c in result.snapshot.document["characters"]}
        assert [entry.subject_id for entry in result.corrections.missing] == [identifier]
        assert any("does not contain it" in warning for warning in result.warnings)


class TestApplyingInIsolation:
    def test_a_work_with_no_corrections_returns_the_document_it_was_given(self, project) -> None:
        store, snapshot, _ = project
        application = correction.apply(store, snapshot.document)

        assert application.document is snapshot.document
        assert application.applied == ()

    def test_applying_does_not_mutate_the_document_it_was_given(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)
        correction.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind="character",
            subject_id=identifier,
            field="name",
            value="Ada Mbeki",
        )

        application = correction.apply(store, snapshot.document)

        assert entry_for(application.document, "character", identifier)["name"] == "Ada Mbeki"
        assert entry_for(snapshot.document, "character", identifier)["name"] == "Ada"


class TestAsJson:
    def test_it_carries_the_standing_corrections_and_this_reading_s_conflicts(
        self, project
    ) -> None:
        store, first, revision_id = project
        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id=a_character(first),
            field="kind",
            value="entity",
        )
        character = store.get_character(a_character(first))
        assert character is not None
        store.upsert_character(
            replace(
                character,
                collection_id=str(first.document["collection"]["id"]),
                kind="collective",
            )
        )
        second = reanalyse(store, revision_id).snapshot

        payload = correction.as_json(store, second.id, second.work_id)

        assert len(payload["corrections"]) == 1
        assert payload["corrections"][0]["field"] == "kind"
        assert len(payload["conflicts"]) == 1
        assert payload["conflicts"][0]["proposed"] == "collective"

    def test_it_names_what_may_be_corrected_so_a_client_need_not_hardcode_it(self, project) -> None:
        store, snapshot, _ = project
        payload = correction.as_json(store, snapshot.id, snapshot.work_id)

        assert payload["correctable"]["character"] == list(correction.fields_for("character"))
        assert payload["correctable"]["relation"] == list(correction.fields_for("relation"))

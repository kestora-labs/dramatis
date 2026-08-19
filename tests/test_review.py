"""Human review of what a reading proposed (5.1).

The properties under test are the four the module is shaped around: a decision is recorded
beside the snapshot and never in it, it is keyed to the claim rather than to the document,
nothing is overwritten, and a claim nobody made cannot be ruled on.

Nothing here touches a network or a model. Review is a person's work.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from dramatis import review
from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.schema import load_schema
from dramatis.store import DDL, ReviewDecision, Store

PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"


def a_reply() -> str:
    return json.dumps(
        {
            "characters": [
                {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram", "Cai")
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
    """An open store holding one analysed work, and the snapshot that analysis produced."""
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


def a_character(snapshot) -> str:
    return str(snapshot.document["characters"][0]["id"])


def a_relation(snapshot) -> str:
    return str(snapshot.document["relations"][0]["id"])


class TestTheVocabulary:
    """Four statuses, written down in three places. They have to agree."""

    def test_the_module_and_the_published_schema_name_the_same_four(self) -> None:
        assert list(review.STATUSES) == load_schema()["$defs"]["reviewStatus"]["enum"]

    def test_the_store_constrains_the_same_four(self) -> None:
        """The `CHECK` on `reviews.status`, read out of the DDL itself.

        A constraint that has drifted from the vocabulary rejects a status the application
        holds to be valid, and the failure arrives as a database error in front of a user.
        """
        block = DDL.split("CREATE TABLE IF NOT EXISTS reviews (")[1].split(");")[0]
        constrained = re.search(r"status\s+TEXT NOT NULL\s+CHECK \(status IN \(([^)]*)\)\)", block)
        assert constrained is not None
        named = [term.strip().strip("'") for term in constrained.group(1).split(",")]
        assert named == list(review.STATUSES)

    def test_a_claim_starts_proposed(self) -> None:
        assert review.DEFAULT_STATUS == "proposed"


class TestWhatCanBeReviewed:
    def test_every_character_and_relation_is_a_subject(self, project) -> None:
        _, snapshot, _ = project
        found = review.subjects(snapshot.document)

        characters = {key for key in found if key[0] == review.CHARACTER}
        relations = {key for key in found if key[0] == review.RELATION}
        assert len(characters) == len(snapshot.document["characters"])
        assert len(relations) == len(snapshot.document["relations"])

    def test_a_relation_is_named_by_its_endpoints(self, project) -> None:
        """A reviewer reading a list needs to know which edge they are ruling on, and
        `rel:ada--bram` is not that."""
        _, snapshot, _ = project
        found = review.subjects(snapshot.document)
        labels = [label for key, label in found.items() if key[0] == review.RELATION]
        assert any("Ada" in label and "--" in label for label in labels)


class TestRecordingADecision:
    def test_a_decision_stands(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=identifier,
            status=review.ACCEPTED,
        )

        entry = review.overlay(store, snapshot).entry_for(review.CHARACTER, identifier)
        assert entry is not None
        assert entry.status == "accepted"
        assert entry.reviewed

    def test_an_edge_is_reviewed_the_same_way_as_a_node(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_relation(snapshot)

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.RELATION,
            subject_id=identifier,
            status=review.REJECTED,
            note="they never actually meet",
        )

        entry = review.overlay(store, snapshot).entry_for(review.RELATION, identifier)
        assert entry is not None
        assert entry.status == "rejected"
        assert entry.note == "they never actually meet"

    def test_the_newest_decision_wins_and_the_earlier_one_survives(self, project) -> None:
        """The whole reason the log is append-only: a reviewer who reverses themselves
        should be able to see that they did."""
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        for status in (review.ACCEPTED, review.REJECTED):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind=review.CHARACTER,
                subject_id=identifier,
                status=status,
            )

        entry = review.overlay(store, snapshot).entry_for(review.CHARACTER, identifier)
        assert entry is not None and entry.status == "rejected"

        past = review.history(store, snapshot.work_id, review.CHARACTER, identifier)
        assert [decision.status for decision in past] == ["accepted", "rejected"]

    def test_restating_the_standing_decision_writes_nothing(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        for _ in range(3):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind=review.CHARACTER,
                subject_id=identifier,
                status=review.ACCEPTED,
            )

        assert len(review.history(store, snapshot.work_id, review.CHARACTER, identifier)) == 1

    def test_the_same_status_with_a_new_reason_is_a_new_decision(self, project) -> None:
        store, snapshot, _ = project
        identifier = a_character(snapshot)

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=identifier,
            status=review.ACCEPTED,
        )
        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=identifier,
            status=review.ACCEPTED,
            note="checked against the cast list",
        )

        past = review.history(store, snapshot.work_id, review.CHARACTER, identifier)
        assert [decision.note for decision in past] == [None, "checked against the cast list"]

    def test_the_snapshot_the_decision_was_taken_in_is_recorded(self, project) -> None:
        store, snapshot, _ = project
        decision = review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=a_character(snapshot),
            status=review.ACCEPTED,
        )
        assert decision.snapshot_id == snapshot.id
        assert decision.work_id == snapshot.work_id


class TestWhatIsRefused:
    def test_a_status_outside_the_vocabulary(self, project) -> None:
        store, snapshot, _ = project
        with pytest.raises(review.ReviewError, match="not a review status"):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind=review.CHARACTER,
                subject_id=a_character(snapshot),
                status="probably-fine",
            )

    def test_something_that_is_neither_a_node_nor_an_edge(self, project) -> None:
        store, snapshot, _ = project
        with pytest.raises(review.ReviewError, match="cannot be reviewed|not something"):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind="evidence",
                subject_id=a_character(snapshot),
                status=review.ACCEPTED,
            )

    def test_a_correction_that_does_not_say_what_it_corrects(self, project) -> None:
        store, snapshot, _ = project
        with pytest.raises(review.ReviewError, match="must say what it corrects"):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind=review.CHARACTER,
                subject_id=a_character(snapshot),
                status=review.CORRECTED,
                note="   ",
            )

    def test_a_correction_with_a_reason_is_recorded(self, project) -> None:
        store, snapshot, _ = project
        decision = review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=a_character(snapshot),
            status=review.CORRECTED,
            note="this is the housekeeper, not the daughter",
        )
        assert decision.status == "corrected"

    def test_a_claim_the_reading_never_made(self, project) -> None:
        store, snapshot, _ = project
        with pytest.raises(review.ReviewError, match="nothing there to review"):
            review.record(
                store,
                snapshot_id=snapshot.id,
                subject_kind=review.CHARACTER,
                subject_id="char:nobody",
                status=review.ACCEPTED,
            )

    def test_a_snapshot_that_does_not_exist(self, project) -> None:
        store, _, _ = project
        with pytest.raises(review.ReviewError, match="no snapshot"):
            review.record(
                store,
                snapshot_id="snap:nothing",
                subject_kind=review.CHARACTER,
                subject_id="char:ada",
                status=review.ACCEPTED,
            )


class TestTheOverlay:
    def test_nothing_ruled_on_is_proposed(self, project) -> None:
        store, snapshot, _ = project
        state = review.overlay(store, snapshot)

        assert len(state) == len(snapshot.document["characters"]) + len(
            snapshot.document["relations"]
        )
        assert all(subject.status == "proposed" for subject in state.subjects)
        assert not any(subject.reviewed for subject in state.subjects)

    def test_counts_name_every_status_including_the_empty_ones(self, project) -> None:
        store, snapshot, _ = project
        state = review.overlay(store, snapshot)
        assert set(state.counts) == set(review.STATUSES)
        assert state.counts["rejected"] == 0

    def test_ruling_on_a_subject_moves_it_between_the_counts(self, project) -> None:
        store, snapshot, _ = project
        before = review.overlay(store, snapshot).counts

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=a_character(snapshot),
            status=review.ACCEPTED,
        )

        after = review.overlay(store, snapshot).counts
        assert after["accepted"] == before["accepted"] + 1
        assert after["proposed"] == before["proposed"] - 1

    def test_the_stored_snapshot_is_not_touched(self, project) -> None:
        """Invariant 4. A review happens after the snapshot was written, and rewriting it
        would change an artifact something may already cite."""
        store, snapshot, _ = project
        before = store.get_snapshot(snapshot.id)
        assert before is not None

        review.record(
            store,
            snapshot_id=snapshot.id,
            subject_kind=review.CHARACTER,
            subject_id=a_character(snapshot),
            status=review.REJECTED,
        )

        after = store.get_snapshot(snapshot.id)
        assert after is not None
        assert after.sha256 == before.sha256
        assert after.document == before.document

    def test_a_status_the_document_declared_is_the_starting_point(self, project) -> None:
        """A snapshot may carry `review_status` itself — the schema has always allowed it.
        Where it does and nobody has ruled since, that is what the overlay reports."""
        store, snapshot, _ = project
        document = json.loads(json.dumps(snapshot.document))
        document["characters"][0]["review_status"] = "accepted"
        stored = store.get_snapshot(snapshot.id)
        assert stored is not None
        amended = replace(stored, document=document)

        state = review.overlay(store, amended)
        entry = state.entry_for(review.CHARACTER, str(document["characters"][0]["id"]))
        assert entry is not None
        assert entry.status == "accepted"
        # Declared by the analysis, not ruled on by a person. The two are different facts.
        assert not entry.reviewed


class TestADecisionOutlivesTheDocumentItWasTakenIn:
    """The keying property: a decision is about the claim, not about the snapshot.

    Carrying human work into a *re-analysis* is **5.2**'s bullet. What is asserted here is
    only what makes that possible — that a decision is not scoped to one document.
    """

    def test_a_second_reading_of_the_same_work_shows_the_standing_decision(self, project) -> None:
        store, first, revision_id = project
        identifier = a_character(first)

        review.record(
            store,
            snapshot_id=first.id,
            subject_kind=review.CHARACTER,
            subject_id=identifier,
            status=review.ACCEPTED,
        )

        second = analyse(
            store,
            revision_id,
            ScriptedProvider([a_reply(), a_grouping()]),
            now="2026-02-02T09:00:00+00:00",
        ).snapshot
        assert second.id != first.id

        entry = review.overlay(store, second).entry_for(review.CHARACTER, identifier)
        assert entry is not None
        assert entry.status == "accepted"
        # And it still says which reading the person was looking at when they decided.
        assert entry.decided_in == first.id


class TestTheStoreItself:
    def test_decisions_come_back_oldest_first(self, project) -> None:
        store, snapshot, _ = project
        for at, status in (("2026-01-02T00:00:00+00:00", "accepted"),):
            store.append_review(
                ReviewDecision(
                    work_id=snapshot.work_id,
                    subject_kind=review.CHARACTER,
                    subject_id="char:x",
                    status=status,
                    snapshot_id=snapshot.id,
                    decided_at=at,
                )
            )
        store.append_review(
            ReviewDecision(
                work_id=snapshot.work_id,
                subject_kind=review.CHARACTER,
                subject_id="char:x",
                status="rejected",
                snapshot_id=snapshot.id,
                decided_at="2026-01-01T00:00:00+00:00",
            )
        )

        recorded = store.list_reviews(snapshot.work_id, subject_id="char:x")
        assert [decision.status for decision in recorded] == ["rejected", "accepted"]
        assert store.current_reviews(snapshot.work_id)[("character", "char:x")].status == "accepted"

    def test_a_work_with_no_decisions_reads_back_empty(self, project) -> None:
        store, snapshot, _ = project
        assert store.list_reviews(snapshot.work_id) == []
        assert store.current_reviews(snapshot.work_id) == {}


class TestAsJson:
    def test_the_document_carries_the_subjects_and_the_tally(self, project) -> None:
        store, snapshot, _ = project
        payload = review.as_json(review.overlay(store, snapshot))

        assert payload["snapshot_id"] == snapshot.id
        assert payload["work_id"] == snapshot.work_id
        assert set(payload["counts"]) == set(review.STATUSES)
        assert len(payload["subjects"]) == len(review.subjects(snapshot.document))

    def test_an_undecided_field_is_present_and_null(self, project) -> None:
        """A client merging this into a map it already holds must be able to clear an
        entry; an absent key cannot say "there is no note"."""
        store, snapshot, _ = project
        subject = review.as_json(review.overlay(store, snapshot))["subjects"][0]
        assert subject["note"] is None
        assert subject["decided_at"] is None
        assert subject["reviewed"] is False

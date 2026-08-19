"""Merging and splitting characters, and the decision the registry records (5.3).

Resolution deliberately cannot merge two characters it already knows: *merging is destructive
and cannot be reviewed after the fact, so it stays a human act*. These are that act, and the
property that matters most is that it needs no special case anywhere else — the registry is
the whole mechanism, and the next reading comes out merged on its own.

The other property under test is that 5.3 does not undo 5.1 and 5.2: a ruling or a correction
recorded against a character that has since been absorbed goes on applying to the one that
absorbed it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis import correction, identity, review
from dramatis.diff import MERGED, SPLIT, diff_snapshots
from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Store

PASSAGE = "Ada met Bram at the gate.\n\nMiss Ada did not answer Bram.\n\nCai spoke to Ada alone.\n"

NAMES = ("Ada", "Miss Ada", "Bram", "Cai")


def a_reply() -> str:
    return json.dumps(
        {
            "characters": [{"name": n, "aliases": [], "kind": "person"} for n in NAMES],
            "interactions": [
                {
                    "participants": ["Ada", "Bram"],
                    "quotation": "Ada met Bram at the gate.",
                    "note": "",
                },
                {
                    "participants": ["Miss Ada", "Bram"],
                    "quotation": "Miss Ada did not answer Bram.",
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
    """Every name its own character — the honest floor, and what leaves two Adas to merge."""
    return json.dumps(
        {
            "groups": [
                {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                for n in NAMES
            ]
        }
    )


@pytest.fixture
def project(tmp_path: Path):
    """A store whose registry holds two characters that are really one person."""
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
        collection_id = str(result.snapshot.document["collection"]["id"])
        yield store, result.snapshot, ingested.revision_id, collection_id


def reanalyse(store, revision_id, *, at="2026-02-02T09:00:00+00:00"):
    return analyse(store, revision_id, ScriptedProvider([a_reply(), a_grouping()]), now=at).snapshot


def cast(document) -> set[str]:
    return {character["id"] for character in document["characters"]}


def weight_of(document, relation_id: str) -> int:
    return next(r["weight"] for r in document["relations"] if r["id"] == relation_id)


class TestMerging:
    def test_the_forms_move_and_the_absorbed_one_is_retired(self, project) -> None:
        store, _, _, collection_id = project

        result = identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        assert "Miss Ada" in result.survivor.aliases
        assert result.survivor.name == "Ada", "the survivor keeps the name the graph calls it"
        assert result.absorbed.retired
        assert result.absorbed.merged_into == "char:ada"
        assert result.absorbed.surface_forms == ("Miss Ada",), (
            "the retired row keeps its own name for a reader tracing it, and claims nothing"
        )

    def test_the_form_now_resolves_to_the_survivor(self, project) -> None:
        """The whole mechanism: nothing else has to be taught anything."""
        store, _, _, collection_id = project

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        found = store.find_character_by_form(collection_id, "Miss Ada")
        assert found is not None and found.id == "char:ada"

    def test_the_retired_character_leaves_the_cast(self, project) -> None:
        store, _, _, collection_id = project
        before = len(store.list_characters(collection_id))

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        assert len(store.list_characters(collection_id)) == before - 1
        # Still traceable, because a snapshot written before the merge names it.
        assert "char:miss-ada" in {
            c.id for c in store.list_characters(collection_id, include_retired=True)
        }

    def test_the_decision_is_recorded(self, project) -> None:
        store, _, _, collection_id = project

        identity.merge(
            store,
            collection_id,
            into="char:ada",
            absorb="char:miss-ada",
            note="the same woman, addressed formally",
        )

        decisions = store.list_registry_decisions(collection_id)
        assert len(decisions) == 1
        assert decisions[0].action == "merge"
        assert decisions[0].source_id == "char:miss-ada"
        assert decisions[0].target_id == "char:ada"
        assert decisions[0].forms == ("Miss Ada",)
        assert decisions[0].note == "the same woman, addressed formally"

    def test_the_survivor_is_marked_corrected(self, project) -> None:
        store, _, _, collection_id = project

        result = identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        assert result.survivor.review_status == "corrected"
        # And still observed: the narrative enacts this character. What a person settled is
        # who it is, not where the claim came from.
        assert result.survivor.provenance == "observed"

    def test_notes_are_taken_only_where_the_survivor_had_none(self, project) -> None:
        from dataclasses import replace

        store, _, _, collection_id = project
        absorbed = store.get_character("char:miss-ada")
        assert absorbed is not None
        store.upsert_character(replace(absorbed, notes="addressed formally by the Bingleys"))

        result = identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        assert result.survivor.notes == "addressed formally by the Bingleys"

    def test_a_character_cannot_absorb_itself(self, project) -> None:
        store, _, _, collection_id = project
        with pytest.raises(identity.IdentityError, match="into itself"):
            identity.merge(store, collection_id, into="char:ada", absorb="char:ada")

    def test_an_unknown_character_is_refused(self, project) -> None:
        store, _, _, collection_id = project
        with pytest.raises(identity.IdentityError, match="not a character"):
            identity.merge(store, collection_id, into="char:ada", absorb="char:nobody")

    def test_a_retired_character_cannot_be_merged_again(self, project) -> None:
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        with pytest.raises(identity.IdentityError, match="already merged"):
            identity.merge(store, collection_id, into="char:bram", absorb="char:miss-ada")


class TestMergingThroughAReanalysis:
    """The acceptance in practice: the graph comes out merged without anything rewriting it."""

    def test_the_next_reading_has_one_character_where_there_were_two(self, project) -> None:
        store, first, revision_id, collection_id = project
        assert {"char:ada", "char:miss-ada"} <= cast(first.document)

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        assert "char:miss-ada" not in cast(second.document)
        assert "char:ada" in cast(second.document)

    def test_the_edges_of_both_become_one_edge(self, project) -> None:
        """Two passages of contact with Bram, counted once each under two names, become two
        under one — which is what the pair was always worth."""
        store, first, revision_id, collection_id = project
        assert weight_of(first.document, "rel:ada--bram") == 1

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        assert weight_of(second.document, "rel:ada--bram") == 2
        assert not [r for r in second.document["relations"] if "miss-ada" in r["id"]]

    def test_the_survivor_lists_the_absorbed_name(self, project) -> None:
        """The record 3.4 reads to recognise a merge, written by the registry as it promised."""
        store, _, revision_id, collection_id = project

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        entry = next(c for c in second.document["characters"] if c["id"] == "char:ada")
        assert "Miss Ada" in entry["aliases"]

    def test_the_diff_reports_a_merge_rather_than_a_disappearance(self, project) -> None:
        """3.4 built this detection for 5.3 and nothing has connected the two until now. A
        character vanishing between drafts is a finding; one being merged is a curation act,
        and reporting the second as the first would be the diff lying about the work."""
        store, first, revision_id, collection_id = project

        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        result = diff_snapshots(first.document, second.document)
        merged = result.characters_of(MERGED)
        assert [change.id for change in merged] == ["char:miss-ada"]
        assert merged[0].counterparts == ("char:ada",)

    def test_a_merge_survives_a_third_reading(self, project) -> None:
        store, _, revision_id, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        reanalyse(store, revision_id)
        third = reanalyse(store, revision_id, at="2026-03-03T09:00:00+00:00")

        assert "char:miss-ada" not in cast(third.document)


class TestSplitting:
    def test_the_named_forms_move_to_a_new_character(self, project) -> None:
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        result = identity.split(store, collection_id, character="char:ada", forms=["Miss Ada"])

        assert result.created.name == "Miss Ada"
        assert result.source.surface_forms == ("Ada",)
        assert "Miss Ada" not in result.source.surface_forms

    def test_a_split_undoes_a_merge(self, project) -> None:
        """The only undo either has, and the reason both are one shape."""
        store, _, revision_id, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        identity.split(store, collection_id, character="char:ada", forms=["Miss Ada"])
        after = reanalyse(store, revision_id)

        names = {c["name"] for c in after.document["characters"]}
        assert {"Ada", "Miss Ada"} <= names

    def test_the_new_character_is_human(self, project) -> None:
        # Unlike a merge, a split puts a node in the graph that no reading proposed.
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        result = identity.split(store, collection_id, character="char:ada", forms=["Miss Ada"])

        assert result.created.provenance == "human"

    def test_it_takes_a_name_of_its_own(self, project) -> None:
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        result = identity.split(
            store,
            collection_id,
            character="char:ada",
            forms=["Miss Ada"],
            name="Ada Mbeki",
        )

        assert result.created.name == "Ada Mbeki"
        assert "Miss Ada" in result.created.aliases

    def test_the_decision_is_recorded(self, project) -> None:
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        identity.split(store, collection_id, character="char:ada", forms=["Miss Ada"])

        actions = [d.action for d in store.list_registry_decisions(collection_id)]
        assert actions == ["merge", "split"]

    def test_moving_every_form_is_refused_as_a_rename(self, project) -> None:
        store, _, _, collection_id = project

        with pytest.raises(identity.IdentityError, match="rename, not a split"):
            identity.split(store, collection_id, character="char:ada", forms=["Ada"])

    def test_a_form_the_character_does_not_answer_to_is_refused(self, project) -> None:
        store, _, _, collection_id = project

        with pytest.raises(identity.IdentityError, match="does not answer to"):
            identity.split(store, collection_id, character="char:ada", forms=["Lizzy"])

    def test_no_forms_at_all_is_refused(self, project) -> None:
        store, _, _, collection_id = project

        with pytest.raises(identity.IdentityError, match="at least one surface form"):
            identity.split(store, collection_id, character="char:ada", forms=["  "])

    def test_a_name_another_character_already_claims_is_refused(self, project) -> None:
        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        with pytest.raises(identity.IdentityError, match="already denotes"):
            identity.split(
                store, collection_id, character="char:ada", forms=["Miss Ada"], name="Bram"
            )

    def test_the_diff_reports_a_split(self, project) -> None:
        store, _, revision_id, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        before = reanalyse(store, revision_id)

        identity.split(store, collection_id, character="char:ada", forms=["Miss Ada"])
        after = reanalyse(store, revision_id, at="2026-03-03T09:00:00+00:00")

        result = diff_snapshots(before.document, after.document)
        assert [change.name for change in result.characters_of(SPLIT)] == ["Miss Ada"]


class TestHumanWorkFollowsTheCharacter:
    """5.3 must not undo 5.1 and 5.2."""

    def test_a_ruling_on_the_absorbed_character_applies_to_the_survivor(self, project) -> None:
        store, first, _, collection_id = project

        review.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:miss-ada",
            status=review.REJECTED,
            note="not a separate person",
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        standing = store.current_reviews(first.work_id)
        assert ("character", "char:ada") in standing
        assert standing[("character", "char:ada")].status == "rejected"

    def test_the_later_ruling_wins_where_both_had_one(self, project) -> None:
        store, first, _, collection_id = project

        review.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:ada",
            status=review.ACCEPTED,
            decided_at="2026-01-02T00:00:00+00:00",
        )
        review.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:miss-ada",
            status=review.REJECTED,
            decided_at="2026-01-03T00:00:00+00:00",
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        standing = store.current_reviews(first.work_id)
        assert standing[("character", "char:ada")].status == "rejected"

    def test_a_correction_to_the_absorbed_character_is_applied_to_the_survivor(
        self, project
    ) -> None:
        store, first, revision_id, collection_id = project

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:miss-ada",
            field="kind",
            value="entity",
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        entry = next(c for c in second.document["characters"] if c["id"] == "char:ada")
        assert entry["kind"] == "entity"
        assert entry["provenance"] == "human"

    def test_a_correction_is_not_stranded_and_reported_as_missing(self, project) -> None:
        """Without the redirect this would warn on every run for ever, about work the person
        has no way to reattach."""
        store, first, revision_id, collection_id = project

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:miss-ada",
            field="notes",
            value="the same woman",
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        result = analyse(
            store,
            revision_id,
            ScriptedProvider([a_reply(), a_grouping()]),
            now="2026-02-02T09:00:00+00:00",
        )

        assert result.corrections.missing == ()

    def test_a_correction_to_an_edge_follows_its_moved_endpoint(self, project) -> None:
        """Merging one end of an edge changes which pair it joins, and so its identifier."""
        store, first, revision_id, collection_id = project

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="relation",
            subject_id="rel:bram--miss-ada",
            field="types",
            value=["estrangement"],
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        second = reanalyse(store, revision_id)

        entry = next(r for r in second.document["relations"] if r["id"] == "rel:ada--bram")
        assert entry["types"] == ["estrangement"]

    def test_a_chain_of_merges_is_followed_all_the_way(self, project) -> None:
        store, first, _, collection_id = project

        review.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:miss-ada",
            status=review.ACCEPTED,
        )
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")
        identity.merge(store, collection_id, into="char:bram", absorb="char:ada")

        standing = store.current_reviews(first.work_id)
        assert ("character", "char:bram") in standing, "a ruling stopped at the first hop"
        assert store.merged_into(collection_id)["char:miss-ada"] == "char:bram"

    def test_an_alias_correction_is_warned_about_rather_than_overruled(self, project) -> None:
        """Two human decisions disagreeing. Picking a winner silently is what this phase
        exists not to do, so the merge says what will happen and proceeds."""
        store, first, _, collection_id = project

        correction.record(
            store,
            snapshot_id=first.id,
            subject_kind="character",
            subject_id="char:ada",
            field="aliases",
            value=["Ada of the gate"],
        )
        result = identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        assert any("standing correction" in warning for warning in result.warnings)


class TestTheRegistryView:
    def test_it_reports_the_decisions_and_who_was_retired(self, project) -> None:
        from dramatis.registry import as_json, build_registry

        store, _, _, collection_id = project
        identity.merge(store, collection_id, into="char:ada", absorb="char:miss-ada")

        payload = as_json(build_registry(store, collection_id))

        assert [entry["action"] for entry in payload["decisions"]] == ["merge"]
        assert payload["retired"] == [
            {"id": "char:miss-ada", "name": "Miss Ada", "merged_into": "char:ada"}
        ]
        assert "char:miss-ada" not in {c["id"] for c in payload["characters"]}


class TestOpeningAStoreMadeBeforeTheColumnExisted:
    """`CREATE TABLE IF NOT EXISTS` adds tables, never columns.

    A project file made before 5.3 has a `characters` table without `merged_into`, and the DDL
    leaves it exactly as it is. Without a migration every query naming the column fails, which
    is every read of the registry — so the first thing an existing project would do on this
    version is break. Found by opening a real store, not by a unit test.
    """

    def _a_store_without_the_column(self, path: Path) -> None:
        import sqlite3

        from dramatis.store import DDL

        # The DDL as it stood before the column: every line naming it dropped, and the comma
        # that used to separate it from the one above with them.
        older = "\n".join(line for line in DDL.splitlines() if "merged_into" not in line)
        older = older.replace("    notes         TEXT,\n", "    notes         TEXT\n")
        assert "merged_into" not in older, "the DDL changed shape; update this test"
        connection = sqlite3.connect(path)
        connection.executescript(older)
        connection.commit()
        connection.close()

    def test_it_opens_and_reads(self, tmp_path: Path) -> None:
        path = tmp_path / "older.sqlite"
        self._a_store_without_the_column(path)

        with Store(path) as store:
            store.upsert_collection("col:a", "A Set")
            assert store.list_characters("col:a") == []
            assert store.merged_into("col:a") == {}

    def test_it_can_then_be_merged_in(self, tmp_path: Path) -> None:
        from dramatis.store import RegisteredCharacter

        path = tmp_path / "older.sqlite"
        self._a_store_without_the_column(path)

        with Store(path) as store:
            store.upsert_collection("col:a", "A Set")
            for identifier, name in (("char:ada", "Ada"), ("char:miss-ada", "Miss Ada")):
                store.upsert_character(
                    RegisteredCharacter(id=identifier, collection_id="col:a", name=name)
                )

            identity.merge(store, "col:a", into="char:ada", absorb="char:miss-ada")

            assert store.merged_into("col:a") == {"char:miss-ada": "char:ada"}

    def test_the_ddl_and_the_migration_name_the_same_columns(self) -> None:
        """Two places describe the schema: what a new store is built from, and what an older
        one is brought up to. A column in one and not the other is a store that works only if
        it happens to be the right age."""
        from dramatis.store import ADDED_COLUMNS, DDL

        for table, column, _definition in ADDED_COLUMNS:
            block = DDL.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
            assert column in block, f"{column!r} is added to old stores but absent from the DDL"

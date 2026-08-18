"""A cast that outlives one work.

A collection is a set of works sharing one character registry. The sharing itself was built
earlier — `_resolve_collection` puts a second work into the collection the project already
holds, and `resolution` matches surface forms against everything the collection knows — but
nothing tested it and nothing could ask where a character appears. Both are what **4.5** is.

The load-bearing case is the last one in `TestOneCastAcrossTwoWorks`: a character introduced
in one novel under one name, referred to in the next by another, and coming back as one
person with one identifier. If that breaks, a shared universe becomes two casts that happen
to rhyme.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.registry import RegistryError, as_json, build_registry
from dramatis.store import Store

BOOK_ONE = "Ada Mbeki met Bram at the gate.\n\nBram did not answer her.\n"
BOOK_TWO = "Chief Mbeki spoke to Cai on the pier.\n\nCai had waited for Chief Mbeki.\n"


def a_reading(characters: list[dict], interactions: list[tuple[tuple[str, str], str]]) -> str:
    return json.dumps(
        {
            "characters": characters,
            "interactions": [
                {"participants": list(pair), "quotation": quotation, "note": ""}
                for pair, quotation in interactions
            ],
        }
    )


def a_grouping(groups: list[dict]) -> str:
    return json.dumps({"groups": groups})


def person(name: str, *aliases: str) -> dict:
    return {"name": name, "aliases": list(aliases), "kind": "person"}


def group(canonical: str, *forms: str, same_as: str = "") -> dict:
    return {
        "canonical_name": canonical,
        "forms": list(forms) or [canonical],
        "kind": "person",
        "same_as_registered": same_as,
    }


def a_saga(tmp_path: Path, store: Store) -> None:
    """Two works in one collection, the second naming a character the first registered."""
    (tmp_path / "one.txt").write_text(BOOK_ONE, encoding="utf-8", newline="")
    (tmp_path / "two.txt").write_text(BOOK_TWO, encoding="utf-8", newline="")

    first = ingest_file(store, tmp_path / "one.txt", work_title="Book One", collection_name="Saga")
    analyse(
        store,
        first.revision_id,
        ScriptedProvider(
            [
                a_reading(
                    [person("Ada Mbeki", "Ada"), person("Bram")],
                    [(("Ada Mbeki", "Bram"), "Ada Mbeki met Bram at the gate.")],
                ),
                a_grouping([group("Ada Mbeki", "Ada Mbeki", "Ada"), group("Bram")]),
            ]
        ),
    )

    second = ingest_file(store, tmp_path / "two.txt", work_title="Book Two")
    analyse(
        store,
        second.revision_id,
        ScriptedProvider(
            [
                a_reading(
                    [person("Chief Mbeki"), person("Cai")],
                    [(("Chief Mbeki", "Cai"), "Chief Mbeki spoke to Cai on the pier.")],
                ),
                a_grouping(
                    [group("Chief Mbeki", same_as="Ada Mbeki"), group("Cai")],
                ),
            ]
        ),
    )


def only_collection(store: Store) -> str:
    return str(store.list_collections()[0]["id"])


class TestOneCastAcrossTwoWorks:
    def test_a_second_work_joins_the_collection_already_there(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

            assert len(store.list_collections()) == 1
            assert len(store.list_works()) == 2

    def test_the_registry_holds_the_whole_cast_of_both(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))

        assert {entry.name for entry in registry.entries} == {"Ada Mbeki", "Bram", "Cai"}

    def test_a_character_named_differently_in_the_second_work_is_the_same_person(
        self, tmp_path: Path
    ) -> None:
        """The one that matters. Ada is introduced as *Ada Mbeki* and called *Chief Mbeki*
        in the next book; if these become two characters, a shared universe is two casts that
        happen to rhyme."""
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))
            ada = next(entry for entry in registry.entries if entry.name == "Ada Mbeki")

        assert ada.spans
        assert len(ada.appearances) == 2
        assert {appearance.work_title for appearance in ada.appearances} == {
            "Book One",
            "Book Two",
        }

    def test_the_second_name_is_kept_as_a_form_of_the_first_character(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            ada = store.find_character_by_form(only_collection(store), "Chief Mbeki")

        assert ada is not None
        assert ada.name == "Ada Mbeki"
        assert "Chief Mbeki" in ada.surface_forms

    def test_a_character_in_one_work_only_does_not_claim_to_span(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))
            bram = next(entry for entry in registry.entries if entry.name == "Bram")

        assert not bram.spans
        assert [appearance.work_title for appearance in bram.appearances] == ["Book One"]

    def test_it_names_who_carries_across_the_collection(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))

        assert [entry.name for entry in registry.spanning] == ["Ada Mbeki"]

    def test_whoever_spans_most_is_listed_first(self, tmp_path: Path) -> None:
        # A reader of a shared-universe registry is looking for who carries across it, which
        # is a different question from the alphabet.
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))

        assert registry.entries[0].name == "Ada Mbeki"


class TestWhatEachClaimRestsOn:
    def test_every_appearance_names_the_snapshot_that_found_it(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))
            known = {
                snapshot.id
                for work_id, _ in registry.works
                for snapshot in store.list_snapshots(work_id)
            }

        for entry in registry.entries:
            for appearance in entry.appearances:
                assert appearance.snapshot_id in known

    def test_only_the_newest_reading_of_a_work_counts(self, tmp_path: Path) -> None:
        """A character in the first draft and cut from the second is not in that novel. A
        registry reading every snapshot ever taken would go on asserting they are."""
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

            # A second revision of Book One in which Bram is gone.
            (tmp_path / "one.txt").write_text(
                "Ada Mbeki walked to the gate alone.\n\nNobody was waiting.\n",
                encoding="utf-8",
                newline="",
            )
            again = ingest_file(store, tmp_path / "one.txt", work_title="Book One")
            analyse(
                store,
                again.revision_id,
                ScriptedProvider(
                    [
                        a_reading([person("Ada Mbeki")], []),
                        a_grouping([group("Ada Mbeki")]),
                    ]
                ),
            )
            registry = build_registry(store, only_collection(store))
            bram = next(entry for entry in registry.entries if entry.name == "Bram")

        assert bram.appearances == (), "Bram was cut, and the newest reading says so"

    def test_a_character_no_current_snapshot_holds_is_still_in_the_registry(
        self, tmp_path: Path
    ) -> None:
        # They are in it because some reading put them there, and dropping them would
        # quietly narrow the cast the next resolution matches against.
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            (tmp_path / "one.txt").write_text(
                "Ada Mbeki walked to the gate alone.\n", encoding="utf-8", newline=""
            )
            again = ingest_file(store, tmp_path / "one.txt", work_title="Book One")
            analyse(
                store,
                again.revision_id,
                ScriptedProvider(
                    [a_reading([person("Ada Mbeki")], []), a_grouping([group("Ada Mbeki")])]
                ),
            )
            registry = build_registry(store, only_collection(store))

        assert "Bram" in {entry.name for entry in registry.entries}

    def test_the_relation_count_is_that_work_alone(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            registry = build_registry(store, only_collection(store))
            ada = next(entry for entry in registry.entries if entry.name == "Ada Mbeki")

        assert all(appearance.relations == 1 for appearance in ada.appearances)


class TestAWorkNobodyHasAnalysed:
    def test_it_is_named_rather_than_silently_absent(self, tmp_path: Path) -> None:
        """A character missing because a work was never read looks exactly like a character
        who is not in it. Only this tells the two apart."""
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            (tmp_path / "three.txt").write_text("Nobody has read this.\n", encoding="utf-8")
            ingest_file(store, tmp_path / "three.txt", work_title="Book Three")
            registry = build_registry(store, only_collection(store))

        assert registry.unanalysed == ("Book Three",)
        assert ("work:book-three", "Book Three") in registry.works

    def test_it_adds_no_appearances(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            (tmp_path / "three.txt").write_text("Nobody has read this.\n", encoding="utf-8")
            ingest_file(store, tmp_path / "three.txt", work_title="Book Three")
            registry = build_registry(store, only_collection(store))
            ada = next(entry for entry in registry.entries if entry.name == "Ada Mbeki")

        assert len(ada.appearances) == 2


class TestReadingNeedsNothing:
    def test_building_the_registry_calls_no_provider(self, tmp_path: Path) -> None:
        # Invariant 6: reading data never requires a model. A provider that would raise on
        # any call is the strongest way to say so.
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            exhausted = ScriptedProvider([])
            registry = build_registry(store, only_collection(store))

        assert len(registry) == 3
        assert exhausted.calls == []

    def test_an_unknown_collection_is_a_clean_error(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store, pytest.raises(RegistryError, match="unknown"):
            build_registry(store, "col:absent")

    def test_a_collection_with_no_works_reads_as_empty(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            store.upsert_collection("col:empty", "Empty")
            registry = build_registry(store, "col:empty")

        assert len(registry) == 0
        assert registry.works == ()


class TestTheRegistryAsADocument:
    def test_it_is_json_serialisable(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            json.dumps(as_json(build_registry(store, only_collection(store))))

    def test_it_carries_the_appearances(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            payload = as_json(build_registry(store, only_collection(store)))

        ada = next(entry for entry in payload["characters"] if entry["name"] == "Ada Mbeki")
        assert ada["spans"] is True
        assert len(ada["appearances"]) == 2
        assert ada["appearances"][0]["work_title"] == "Book One"

    def test_it_carries_the_forms_the_second_work_contributed(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            payload = as_json(build_registry(store, only_collection(store)))

        ada = next(entry for entry in payload["characters"] if entry["name"] == "Ada Mbeki")
        assert "Chief Mbeki" in ada["aliases"]


class TestTheCommandAndTheEndpoint:
    """Both read-only paths onto the registry. Neither may need a model (Invariant 6)."""

    def test_the_command_lists_who_spans_and_where(self, tmp_path: Path, capsys) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        assert main(["characters", "--store", str(tmp_path / "p.sqlite")]) == 0
        out = capsys.readouterr().out
        assert "Ada Mbeki" in out
        assert "Book One" in out and "Book Two" in out

    def test_the_command_can_show_only_those_who_span(self, tmp_path: Path, capsys) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        main(["characters", "--store", str(tmp_path / "p.sqlite"), "--spanning"])
        out = capsys.readouterr().out
        assert "Ada Mbeki" in out
        assert "Bram" not in out

    def test_the_command_names_a_work_nobody_has_analysed(self, tmp_path: Path, capsys) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            (tmp_path / "three.txt").write_text("Unread.\n", encoding="utf-8")
            ingest_file(store, tmp_path / "three.txt", work_title="Book Three")

        main(["characters", "--store", str(tmp_path / "p.sqlite")])
        assert "Book Three" in capsys.readouterr().err

    def test_the_command_says_one_relation_rather_than_1_relations(
        self, tmp_path: Path, capsys
    ) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        main(["characters", "--store", str(tmp_path / "p.sqlite")])
        assert "1 relations" not in capsys.readouterr().out

    def test_the_command_output_survives_a_legacy_console(self, tmp_path: Path, capsys) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)
            (tmp_path / "three.txt").write_text("Unread.\n", encoding="utf-8")
            ingest_file(store, tmp_path / "three.txt", work_title="Book Three")

        main(["characters", "--store", str(tmp_path / "p.sqlite")])
        captured = capsys.readouterr()
        captured.out.encode("ascii")
        captured.err.encode("ascii")

    def test_the_command_json_is_parseable(self, tmp_path: Path, capsys) -> None:
        from dramatis.cli import main

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        main(["characters", "--store", str(tmp_path / "p.sqlite"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["collection"]["name"] == "Saga"

    def test_the_endpoint_serves_the_registry(self, tmp_path: Path) -> None:
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from dramatis.server import create_app

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        client = TestClient(create_app(tmp_path / "p.sqlite"))
        payload = client.get("/api/registry").json()

        ada = next(entry for entry in payload["characters"] if entry["name"] == "Ada Mbeki")
        assert ada["spans"] is True
        assert {a["work_title"] for a in ada["appearances"]} == {"Book One", "Book Two"}

    def test_the_endpoint_refuses_a_collection_it_does_not_hold(self, tmp_path: Path) -> None:
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from dramatis.server import create_app

        with Store(tmp_path / "p.sqlite") as store:
            a_saga(tmp_path, store)

        client = TestClient(create_app(tmp_path / "p.sqlite"))
        assert (
            client.get("/api/registry", params={"collection_id": "col:absent"}).status_code == 404
        )

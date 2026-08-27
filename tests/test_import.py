"""Reading a Dramatis document somebody else produced (**6.3**).

Phase 6 is accepted in part on this sentence: *"A snapshot exported and re-imported is
byte-identical after normalisation."* That is the first test here, and it is the weakest one
— the store keeps the rendered document, so handing it back is most of what byte-identity
proves. The tests that carry the weight are the other two kinds:

**Is the imported project usable?** A reading that arrives as an opaque blob is not
interoperability. Its cast has to be in the registry, its surface forms claimed, its work and
revision real enough that every other command works against them.

**What does it refuse, and does it refuse before writing?** An import that fails halfway
leaves a project holding half a reading, which looks exactly like a reading somebody meant to
have. Every refusal below is checked twice: that it refused, and that the store is untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dramatis.export import SNAPSHOT, export_document
from dramatis.importer import ImportRefused, import_document, import_file
from dramatis.snapshot import canonical_json
from dramatis.store import Store
from tests.documents import minimal_document


def a_document(**changes: Any) -> dict[str, Any]:
    document = minimal_document()
    document.update(changes)
    return document


def a_store(tmp_path: Path, name: str = "dramatis.sqlite") -> Path:
    path = tmp_path / name
    with Store(path):
        pass
    return path


def written(path: Path) -> dict[str, int]:
    """What a project holds, in the four counts a half-import would disturb."""
    with Store(path) as store:
        works = store.list_works()
        return {
            "collections": len(store.list_collections()),
            "works": len(works),
            "characters": store.count("characters"),
            "snapshots": sum(len(store.list_snapshots(work["id"])) for work in works),
        }


# -- the acceptance criterion ---------------------------------------------------------


class TestTheRoundTrip:
    def test_export_import_export_is_byte_identical(self, tmp_path: Path) -> None:
        """*"A snapshot exported and re-imported is byte-identical after normalisation."*

        The normalisation is `canonical_json`, which the `snapshot` format writes: sorted
        keys and no incidental whitespace, so two machines exporting one reading produce one
        file.
        """
        document = a_document()
        first = export_document(document, SNAPSHOT).parts[0].text

        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, json.loads(first))
            stored = store.get_snapshot("snap:1")

        assert stored is not None
        second = export_document(stored.document, SNAPSHOT).parts[0].text

        assert first == second

    def test_the_normalised_form_does_not_depend_on_key_order(self, tmp_path: Path) -> None:
        """A document from another tool will not have Dramatis's key order, and byte-identity
        after normalisation is a claim about the normal form rather than about the input."""
        document = a_document()
        shuffled = json.loads(json.dumps(dict(reversed(list(document.items())))))

        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, shuffled)
            stored = store.get_snapshot("snap:1")

        assert stored is not None
        assert canonical_json(stored.document) == canonical_json(document)


# -- the imported project has to work -------------------------------------------------


class TestWhatArrives:
    def test_the_cast_is_registered_rather_than_only_stored(self, tmp_path: Path) -> None:
        """Otherwise the import is a blob in a table, and every other command sees nothing."""
        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, a_document())

            registered = {c.id: c.name for c in store.list_characters("col:test")}

        assert registered == {"char:a": "Ada", "char:b": "Bram"}

    def test_surface_forms_are_claimed_so_a_later_reading_resolves_to_them(
        self, tmp_path: Path
    ) -> None:
        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, a_document())

            found = store.find_character_by_form("col:test", "Miss A")

        assert found is not None
        assert found.id == "char:a"

    def test_the_work_and_its_revision_arrive(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, a_document())

            work = store.get_work("work:1")
            revision = store.get_text_revision("rev:1")

        assert work is not None and work["title"] == "A Work"
        assert revision is not None and revision.document_ids == ("doc:1",)

    def test_it_reports_what_it_did(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        with Store(path) as store:
            result = import_document(store, a_document())

        assert result.snapshot_id == "snap:1"
        assert (result.characters, result.relations, result.evidence) == (2, 1, 1)
        assert result.documents_recorded == 1
        assert not result.already_present

    def test_importing_the_same_document_twice_changes_nothing(self, tmp_path: Path) -> None:
        """People re-run commands. The second one should cost nothing and say so."""
        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, a_document())
            before = written(path)
            again = import_document(store, a_document())

        assert again.already_present
        assert "already in this project" in again.summary
        assert written(path) == before


# -- the text is not in the file, and that has consequences ---------------------------


class TestDocumentsArriveWithoutTheirText:
    def test_a_document_is_recorded_with_no_content(self, tmp_path: Path) -> None:
        """The schema carries a hash per document and never the text. That is what makes a
        Dramatis file safe to send, and it is why a passage will not open."""
        path = a_store(tmp_path)
        with Store(path) as store:
            import_document(store, a_document())

            stored = store.get_document("doc:1")

        assert stored is not None
        assert stored.content == ""
        assert stored.sha256 == "0" * 64

    def test_an_existing_document_is_never_overwritten_by_a_placeholder(
        self, tmp_path: Path
    ) -> None:
        """The data-loss guard, and the reason this does not call `upsert_document` blindly:
        that sets `content` from what it is given, so importing over a document the project
        already has would replace the only copy of the text with an empty string."""
        path = a_store(tmp_path)
        document = a_document()

        with Store(path) as store:
            from dramatis.store import Document

            store.upsert_collection("col:test", "Test collection")
            store.upsert_work("work:1", "col:test", "A Work")
            store.upsert_document(
                Document(
                    id="doc:1",
                    work_id="work:1",
                    role="narrative",
                    sha256="0" * 64,
                    content="They met at the gate.",
                )
            )

            result = import_document(store, document)
            after = store.get_document("doc:1")

        assert after is not None
        assert after.content == "They met at the gate."
        assert result.documents_already_here == 1
        assert result.documents_recorded == 0


# -- refusals, and nothing written ----------------------------------------------------


class TestWhatItRefuses:
    def test_a_document_the_schema_rejects(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        document = a_document()
        document["relations"][0]["source"] = "char:missing"

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_document(store, document)

        assert "schema" in str(raised.value)
        assert written(path)["snapshots"] == 0

    def test_a_different_reading_wearing_an_identifier_that_is_taken(self, tmp_path: Path) -> None:
        """Invariant 4: snapshots are immutable, so two readings cannot share an identifier."""
        path = a_store(tmp_path)
        other = a_document()
        other["relations"][0]["weight"] = 99

        with Store(path) as store:
            import_document(store, a_document())
            before = written(path)

            with pytest.raises(ImportRefused) as raised:
                import_document(store, other)

        assert "immutable" in str(raised.value)
        assert written(path) == before

    def test_an_identifier_that_already_means_somebody_else(self, tmp_path: Path) -> None:
        """Merging them would silently make two people one, and renaming would break every
        relation that names them. Deciding they are the same person is `dramatis merge`."""
        path = a_store(tmp_path)
        intruder = a_document()
        intruder["snapshot"] = {**intruder["snapshot"], "id": "snap:2", "analysis_run_id": "run:2"}
        intruder["analysis_runs"] = [{"id": "run:2", "model": "m", "prompt_version": "p"}]
        intruder["characters"] = [
            {"id": "char:a", "name": "Adelaide", "provenance": "observed"},
            {"id": "char:c", "name": "Cai", "provenance": "observed"},
        ]
        intruder["relations"][0].update({"id": "rel:a-c", "source": "char:a", "target": "char:c"})

        with Store(path) as store:
            import_document(store, a_document())
            before = written(path)

            with pytest.raises(ImportRefused) as raised:
                import_document(store, intruder)

            assert store.get_character("char:c") is None

        assert "Ada" in str(raised.value) and "Adelaide" in str(raised.value)
        assert written(path) == before

    def test_a_surface_form_another_character_already_claims(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        intruder = a_document()
        intruder["snapshot"] = {**intruder["snapshot"], "id": "snap:2", "analysis_run_id": "run:2"}
        intruder["analysis_runs"] = [{"id": "run:2", "model": "m", "prompt_version": "p"}]
        intruder["characters"] = [
            {"id": "char:c", "name": "Cai", "aliases": ["Miss A"], "provenance": "observed"},
            {"id": "char:d", "name": "Dov", "provenance": "observed"},
        ]
        intruder["relations"][0].update({"id": "rel:c-d", "source": "char:c", "target": "char:d"})

        with Store(path) as store:
            import_document(store, a_document())
            before = written(path)

            with pytest.raises(ImportRefused) as raised:
                import_document(store, intruder)

            # The point of the pre-flight: `upsert_character` raises on the first clash it
            # meets, by which time the characters before it are already written.
            assert store.get_character("char:c") is None
            assert store.get_character("char:d") is None

        assert "Miss A" in str(raised.value)
        assert written(path) == before

    def test_a_document_that_gives_one_form_to_two_characters(self, tmp_path: Path) -> None:
        """Refused on its own terms, with no help from the store: the file is incoherent."""
        path = a_store(tmp_path)
        document = a_document()
        document["characters"][1]["aliases"] = ["Miss A"]

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_document(store, document)

        assert "cannot denote two characters" in str(raised.value)
        assert written(path)["snapshots"] == 0

    def test_a_snapshot_of_a_work_the_document_does_not_describe(self, tmp_path: Path) -> None:
        """The validator owns this one, which is why the importer does not check it twice:
        a snapshot naming a work the document never carries is a reference failure."""
        path = a_store(tmp_path)
        document = a_document()
        document["works"] = [{"id": "work:other", "title": "Another"}]
        document["text_revisions"][0]["work_id"] = "work:other"
        document["documents"][0]["work_id"] = "work:other"

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_document(store, document)

        assert "unknown work" in str(raised.value)
        assert written(path)["snapshots"] == 0


class TestReadingTheFile:
    def test_a_file_that_is_not_json(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        source = tmp_path / "notes.json"
        source.write_text("this is not json", encoding="utf-8")

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_file(store, source)

        assert "not JSON" in str(raised.value)

    def test_a_file_holding_something_that_is_not_a_document(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)
        source = tmp_path / "list.json"
        source.write_text("[1, 2, 3]", encoding="utf-8")

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_file(store, source)

        assert "not a Dramatis document" in str(raised.value)

    def test_a_file_that_is_not_there(self, tmp_path: Path) -> None:
        path = a_store(tmp_path)

        with Store(path) as store, pytest.raises(ImportRefused) as raised:
            import_file(store, tmp_path / "absent.json")

        assert "could not be read" in str(raised.value)

"""Tests for ingest and the store.

The property that matters most here is idempotence. A revision identifier is derived from
the content hash, so ingesting the same text twice must produce the same revision and leave
the store no larger. Without that, re-ingesting a folder quietly duplicates everything and
every later diff is against the wrong baseline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dramatis import ids
from dramatis.ingest import IngestError, ingest_file, read_text
from dramatis.store import Store
from dramatis.text import content_hash

SAMPLE = "It is a truth universally acknowledged,\nthat a single man in possession\n"


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "project.sqlite") as opened:
        yield opened


def write(tmp_path: Path, name: str, text: str = SAMPLE) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


# -- reading ---------------------------------------------------------------------------


class TestReadText:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        path = write(tmp_path, "a.txt", "Zoë walked out.\n")
        assert read_text(path) == "Zoë walked out.\n"

    def test_strips_byte_order_mark(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.txt"
        path.write_bytes("﻿First line\n".encode())
        assert read_text(path).startswith("First")

    def test_normalises_line_endings(self, tmp_path: Path) -> None:
        path = write(tmp_path, "crlf.txt", "a\r\nb\r\n")
        assert read_text(path) == "a\nb\n"

    def test_missing_file_is_a_clean_error(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="no such file"):
            read_text(tmp_path / "absent.txt")

    def test_directory_is_a_clean_error(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="phase 4"):
            read_text(tmp_path)

    def test_empty_file_is_rejected(self, tmp_path: Path) -> None:
        path = write(tmp_path, "empty.txt", "   \n\n")
        with pytest.raises(IngestError, match="empty"):
            read_text(path)

    def test_non_utf8_is_rejected_rather_than_guessed(self, tmp_path: Path) -> None:
        """Guessing an encoding wrong corrupts quotations in ways nothing downstream sees."""
        path = tmp_path / "latin1.txt"
        path.write_bytes("Café".encode("latin-1"))
        with pytest.raises(IngestError, match="not valid UTF-8"):
            read_text(path)


# -- ingesting -------------------------------------------------------------------------


class TestIngestFile:
    def test_records_a_revision_with_the_content_hash(self, store: Store, tmp_path: Path) -> None:
        result = ingest_file(store, write(tmp_path, "pride.txt"))

        assert result.sha256 == content_hash(SAMPLE)
        assert result.revision_id == ids.revision_id(result.sha256)
        assert result.characters == len(SAMPLE)
        assert result.already_present is False

    def test_revision_is_retrievable_and_carries_its_document(
        self, store: Store, tmp_path: Path
    ) -> None:
        result = ingest_file(store, write(tmp_path, "pride.txt"))

        revision = store.get_text_revision(result.revision_id)
        assert revision is not None
        assert revision.sha256 == result.sha256
        assert revision.document_ids == (result.document_id,)

    def test_document_content_is_stored_not_merely_referenced(
        self, store: Store, tmp_path: Path
    ) -> None:
        """Evidence that cannot be resolved back to its text is not evidence."""
        result = ingest_file(store, write(tmp_path, "pride.txt"))

        document = store.get_document(result.document_id)
        assert document is not None
        assert document.content == SAMPLE
        assert store.revision_text(result.revision_id) == SAMPLE

    def test_titles_default_to_the_filename(self, store: Store, tmp_path: Path) -> None:
        result = ingest_file(store, write(tmp_path, "the_salt_road.txt"))

        work = store.get_work(result.work_id)
        assert work is not None
        assert work["title"] == "the salt road"

    def test_explicit_metadata_is_recorded(self, store: Store, tmp_path: Path) -> None:
        result = ingest_file(
            store,
            write(tmp_path, "pp.txt"),
            work_title="Pride and Prejudice",
            collection_name="Jane Austen",
            creator="Jane Austen",
            language="en",
            label="As published",
        )

        work = store.get_work(result.work_id)
        assert work is not None
        assert work["title"] == "Pride and Prejudice"
        assert work["creator"] == "Jane Austen"
        assert work["language"] == "en"
        assert result.collection_id == ids.collection_id("Jane Austen")

        revision = store.get_text_revision(result.revision_id)
        assert revision is not None
        assert revision.label == "As published"

    def test_a_standalone_work_is_a_collection_of_one(self, store: Store, tmp_path: Path) -> None:
        result = ingest_file(store, write(tmp_path, "pp.txt"), work_title="Pride and Prejudice")
        assert result.collection_id == ids.collection_id("Pride and Prejudice")

    def test_unknown_role_is_rejected(self, store: Store, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="unknown document role"):
            ingest_file(store, write(tmp_path, "pp.txt"), role="appendix")

    def test_reference_role_is_accepted(self, store: Store, tmp_path: Path) -> None:
        result = ingest_file(store, write(tmp_path, "cast.txt"), role="reference")
        document = store.get_document(result.document_id)
        assert document is not None
        assert document.role == "reference"


class TestIdempotence:
    def test_ingesting_the_same_text_twice_yields_the_same_revision(
        self, store: Store, tmp_path: Path
    ) -> None:
        first = ingest_file(store, write(tmp_path, "pride.txt"))
        second = ingest_file(store, write(tmp_path, "pride.txt"))

        assert first.revision_id == second.revision_id
        assert second.already_present is True

    def test_re_ingest_does_not_grow_the_store(self, store: Store, tmp_path: Path) -> None:
        path = write(tmp_path, "pride.txt")
        ingest_file(store, path)
        ingest_file(store, path)
        ingest_file(store, path)

        counts = {
            table: store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("collections", "works", "documents", "text_revisions")
        }
        assert counts == {
            "collections": 1,
            "works": 1,
            "documents": 1,
            "text_revisions": 1,
        }

    def test_re_ingest_keeps_the_work_in_its_existing_collection(
        self, store: Store, tmp_path: Path
    ) -> None:
        """The collection scopes the character registry, so a silent move is corrupting."""
        path = write(tmp_path, "pride.txt")
        first = ingest_file(
            store, path, work_title="Pride and Prejudice", collection_name="Jane Austen"
        )
        second = ingest_file(store, path, work_title="Pride and Prejudice")

        assert second.collection_id == first.collection_id

        work = store.get_work(first.work_id)
        assert work is not None
        assert work["collection_id"] == ids.collection_id("Jane Austen")

    def test_re_ingest_does_not_leave_an_orphaned_collection(
        self, store: Store, tmp_path: Path
    ) -> None:
        path = write(tmp_path, "pride.txt")
        ingest_file(store, path, work_title="Pride and Prejudice", collection_name="Jane Austen")
        ingest_file(store, path, work_title="Pride and Prejudice")

        count = store.connection.execute("SELECT count(*) FROM collections").fetchone()[0]
        assert count == 1

    def test_naming_a_different_collection_is_refused(self, store: Store, tmp_path: Path) -> None:
        """A project holds one collection, so there is nowhere else in it to move a work.

        Phase 1.1 allowed an explicit collection to move a work between collections in one
        store. Phase 1.10 removed the destination: the registry is collection-scoped, and a
        store with two collections holds two casts that cannot see each other. Use two
        project files instead.
        """
        path = write(tmp_path, "pride.txt")
        ingest_file(store, path, work_title="Pride and Prejudice", collection_name="Jane Austen")

        with pytest.raises(IngestError, match="holds one collection"):
            ingest_file(
                store, path, work_title="Pride and Prejudice", collection_name="Regency Novels"
            )

        work = store.get_work(ids.work_id("Pride and Prejudice"))
        assert work is not None
        assert work["collection_id"] == ids.collection_id("Jane Austen"), "unmoved"

    def test_naming_the_existing_collection_is_accepted(self, store: Store, tmp_path: Path) -> None:
        first = ingest_file(
            store, write(tmp_path, "a.txt", "One.\n"), work_title="A", collection_name="A Universe"
        )
        second = ingest_file(
            store, write(tmp_path, "b.txt", "Two.\n"), work_title="B", collection_name="A Universe"
        )

        assert second.collection_id == first.collection_id


class TestOneCollectionPerProject:
    """What makes a shared universe work: several works, one registry."""

    def test_a_second_work_joins_the_project_collection(self, store: Store, tmp_path: Path) -> None:
        first = ingest_file(
            store,
            write(tmp_path, "meteor.txt", "Rhoda appears.\n"),
            work_title="Meteor Girl",
            collection_name="A Shared Universe",
        )
        second = ingest_file(
            store, write(tmp_path, "spark.txt", "Rhoda again.\n"), work_title="The Spark"
        )

        assert second.collection_id == first.collection_id, "one registry across both works"
        assert len(store.list_collections()) == 1
        assert {w["title"] for w in store.list_works()} == {"Meteor Girl", "The Spark"}

    def test_the_refusal_names_both_collections_and_says_what_to_do(
        self, store: Store, tmp_path: Path
    ) -> None:
        ingest_file(
            store, write(tmp_path, "a.txt", "One.\n"), work_title="A", collection_name="Universe A"
        )

        with pytest.raises(IngestError) as failure:
            ingest_file(
                store,
                write(tmp_path, "b.txt", "Two.\n"),
                work_title="B",
                collection_name="Universe B",
            )

        message = str(failure.value)
        assert "Universe A" in message and "Universe B" in message
        assert "separate project file" in message

    def test_an_empty_project_takes_whatever_it_is_given(
        self, store: Store, tmp_path: Path
    ) -> None:
        result = ingest_file(
            store, write(tmp_path, "a.txt"), work_title="A", collection_name="Anything At All"
        )
        assert result.collection_id == ids.collection_id("Anything At All")

    def test_the_same_text_under_a_different_filename_is_the_same_revision(
        self, store: Store, tmp_path: Path
    ) -> None:
        """The revision identifies the text, not the file it arrived in."""
        first = ingest_file(store, write(tmp_path, "draft.txt"), work_title="W")
        second = ingest_file(store, write(tmp_path, "final.txt"), work_title="W")

        assert first.revision_id == second.revision_id

    def test_line_ending_differences_are_the_same_revision(
        self, store: Store, tmp_path: Path
    ) -> None:
        unix = ingest_file(store, write(tmp_path, "unix.txt", "a\nb\n"), work_title="W")
        windows = ingest_file(store, write(tmp_path, "dos.txt", "a\r\nb\r\n"), work_title="W")

        assert unix.revision_id == windows.revision_id

    def test_edited_text_is_a_new_revision(self, store: Store, tmp_path: Path) -> None:
        first = ingest_file(store, write(tmp_path, "v1.txt", "a\nb\n"), work_title="W")
        second = ingest_file(store, write(tmp_path, "v2.txt", "a\nc\n"), work_title="W")

        assert first.revision_id != second.revision_id
        assert len(store.list_text_revisions(first.work_id)) == 2


class TestStore:
    def test_a_new_store_records_its_format_version(self, tmp_path: Path) -> None:
        with Store(tmp_path / "new.sqlite") as store:
            assert store.store_version >= 1

    def test_reopening_preserves_content(self, tmp_path: Path) -> None:
        path = tmp_path / "project.sqlite"
        with Store(path) as store:
            result = ingest_file(store, write(tmp_path, "pride.txt"))

        with Store(path) as reopened:
            assert reopened.get_text_revision(result.revision_id) is not None

    def test_opening_an_existing_store_is_not_destructive(self, tmp_path: Path) -> None:
        """The DDL is idempotent; opening an older store adds what is missing."""
        path = tmp_path / "project.sqlite"
        with Store(path) as store:
            ingest_file(store, write(tmp_path, "pride.txt"))
        with Store(path) as store:
            ingest_file(store, write(tmp_path, "other.txt", "different text\n"))
        with Store(path) as store:
            count = store.connection.execute("SELECT count(*) FROM text_revisions").fetchone()[0]
        assert count == 2

    def test_using_a_closed_store_fails_loudly(self, tmp_path: Path) -> None:
        store = Store(tmp_path / "closed.sqlite")
        with pytest.raises(RuntimeError, match="not open"):
            _ = store.connection

    def test_parent_directories_are_created(self, tmp_path: Path) -> None:
        with Store(tmp_path / "nested" / "deeper" / "p.sqlite") as store:
            assert store.store_version >= 1


class TestProjectSettings:
    """A project holds the terms it is studied under, not only its data (D17)."""

    def test_an_unset_setting_returns_the_default(self, store: Store) -> None:
        assert store.get_setting("collectives_are_actors") is None
        assert store.get_setting("collectives_are_actors", default=False) is False

    def test_a_setting_deliberately_set_to_nothing_is_still_a_recorded_choice(
        self, store: Store
    ) -> None:
        """`get_setting` cannot tell this from never having been set, since both answer
        None. `settings()` can, and is what a caller asks when the difference matters."""
        store.set_setting("collectives_are_actors", None)

        assert store.get_setting("collectives_are_actors") is None
        assert store.settings() == {"collectives_are_actors": None}

    @pytest.mark.parametrize("value", [True, False, "asserted", 3, ["a", "b"]])
    def test_a_setting_reads_back_as_the_type_it_was_written_as(
        self, store: Store, value: object
    ) -> None:
        """A switch stored as the string "false" is true, and would analyse a corpus the
        wrong way round without anything looking wrong."""
        store.set_setting("some_setting", value)

        assert store.get_setting("some_setting") == value
        assert type(store.get_setting("some_setting")) is type(value)

    def test_a_setting_survives_reopening(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.sqlite"
        with Store(path) as store:
            store.set_setting("collectives_are_actors", True)
        with Store(path) as reopened:
            assert reopened.get_setting("collectives_are_actors") is True

    def test_setting_a_second_time_replaces_the_value(self, store: Store) -> None:
        """Whether a *particular* setting may change is policy that belongs with the
        setting. Storage does not decide it."""
        store.set_setting("collectives_are_actors", True)
        store.set_setting("collectives_are_actors", False)

        assert store.get_setting("collectives_are_actors") is False
        assert list(store.settings()) == ["collectives_are_actors"]

    def test_settings_are_listed_without_the_store_s_own_keys(self, store: Store) -> None:
        """store_version is a fact about the file that nobody decided. Listing it beside
        the decisions would present it as one."""
        store.set_setting("collectives_are_actors", True)

        assert store.settings() == {"collectives_are_actors": True}
        assert store.store_version >= 1

    def test_a_setting_cannot_collide_with_a_store_key(self, store: Store) -> None:
        store.set_setting("store_version", "not the real one")

        assert store.store_version >= 1
        assert store.get_setting("store_version") == "not the real one"

    def test_settings_are_empty_on_a_fresh_project(self, store: Store) -> None:
        assert store.settings() == {}

    @pytest.mark.parametrize("name", ["", " ", " padded", "trailing "])
    def test_an_unusable_setting_name_is_refused(self, store: Store, name: str) -> None:
        with pytest.raises(ValueError, match="non-empty and unpadded"):
            store.set_setting(name, True)

    def test_passing_the_stored_key_instead_of_the_name_is_refused(self, store: Store) -> None:
        """Otherwise it writes setting:setting:x and reads back as absent."""
        with pytest.raises(ValueError, match="not the stored key"):
            store.set_setting("setting:collectives_are_actors", True)


class TestIdentifiers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Pride and Prejudice", "pride-and-prejudice"),
            ("  Spaced  Out  ", "spaced-out"),
            ("Zoë Ashe", "zoe-ashe"),
            ("The Salt Road!!", "the-salt-road"),
            ("under_scores/and\\slashes", "under-scores-and-slashes"),
            ("--already--hyphenated--", "already-hyphenated"),
        ],
    )
    def test_slugify(self, value: str, expected: str) -> None:
        assert ids.slugify(value) == expected

    def test_unsluggable_titles_still_produce_an_identifier(self) -> None:
        assert ids.work_id("日本語") == "work:untitled"

    def test_revision_identifier_is_derived_from_the_hash(self) -> None:
        digest = content_hash("some text")
        assert ids.revision_id(digest) == f"rev:{digest[:12]}"

    def test_identifiers_are_namespaced_by_kind(self) -> None:
        assert ids.collection_id("A").startswith("col:")
        assert ids.work_id("A").startswith("work:")
        assert ids.document_id("A").startswith("doc:")

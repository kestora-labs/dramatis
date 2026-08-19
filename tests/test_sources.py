"""A corpus source is an interface, and the local filesystem is one implementation (4.12).

Two things are being proven here, and the second is the point of the bullet.

The first is that `FileSystemSource` answers the two questions the same way the two inline
directory walks it replaced did: one stable root however the corpus was named, and readable
documents as `(path, text)` pairs with everything else skipped *and named*.

The second is that nothing downstream needs a filesystem any more. `InMemorySource` below
touches no disk and could as well be a network client. Ingesting through it produces the
same revision, the same document identifiers and the same hashes as ingesting the folder it
mirrors; proposing a structure over it produces the same map; a confirmed excluded region is
dropped from it exactly as from a file. If any of that stops being true, the next bullet's
Drive source would have to reach back into `ingest`, which is what this bullet exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from dramatis.ingest import ingest_folder, ingest_source
from dramatis.sources import FileSystemSource, IngestError, Reading, Source, as_source
from dramatis.store import EXCLUDED, NARRATIVE, Store
from dramatis.structure import as_json, propose_structure

CHAPTER_ONE = "Ada met Bram at the gate.\n"
CHAPTER_TWO = "Bram did not remember her.\n"
CAST = "Ada is Bram's sister.\n"

PREFACE = "PREFACE\n\nAn editor who admired Coleridge says so at length.\n\n"
NOVEL = "It is a truth universally acknowledged.\n\nAda met Bram at the gate.\n"


@dataclass(frozen=True)
class InMemorySource:
    """A source with no filesystem behind it at all.

    Deliberately the dullest possible one. It exists so the tests can assert that the pairs
    are the whole of what ingest and structure inference need — a claim that cannot be made
    with a source that still has a directory under it.
    """

    identifier: str
    pairs: tuple[tuple[str, str], ...]
    unreadable: tuple[tuple[str, str], ...] = ()

    @property
    def root(self) -> str:
        return self.identifier

    def read(self) -> Reading:
        return Reading(documents=self.pairs, skipped=self.unreadable)


def a_folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return a_folder(
        tmp_path / "corpus",
        {
            "cast.md": CAST,
            "draft/chapter-01.md": CHAPTER_ONE,
            "draft/chapter-02.md": CHAPTER_TWO,
        },
    )


class TestTheRootIsStable:
    """The root is the key a confirmed structure map is saved under.

    If it moved with how the corpus was named, a person who ingested `corpus` and then
    `./corpus` would be asked to confirm the same documents twice, and their first answers
    would sit in the store attached to a root nothing would ever look up again.
    """

    def test_every_way_of_naming_one_folder_gives_one_root(self, corpus: Path) -> None:
        by_absolute = FileSystemSource(corpus).root
        by_string = FileSystemSource(str(corpus)).root
        by_detour = FileSystemSource(corpus.parent / "." / corpus.name).root

        assert by_absolute == by_string == by_detour

    def test_a_file_is_its_own_root_and_not_its_folders(self, corpus: Path) -> None:
        # 4.11's single-file corpus. A file and the folder holding it must not share a root,
        # or excluding a preface in one would silently apply to the other.
        assert FileSystemSource(corpus / "cast.md").root != FileSystemSource(corpus).root

    def test_a_path_means_the_local_filesystem(self, corpus: Path) -> None:
        assert as_source(corpus).root == FileSystemSource(corpus).root
        assert as_source(str(corpus)).root == FileSystemSource(corpus).root

    def test_a_source_is_passed_through_untouched(self) -> None:
        source = InMemorySource("memory:one", (("a.md", CAST),))
        assert as_source(source) is source


class TestWhatAFolderOffers:
    def test_documents_are_path_and_text_in_a_deterministic_order(self, corpus: Path) -> None:
        # The order decides the revision hash, so it is sorted by relative path rather than
        # left to whatever the filesystem happened to hand back.
        assert FileSystemSource(corpus).read().documents == (
            ("cast.md", CAST),
            ("draft/chapter-01.md", CHAPTER_ONE),
            ("draft/chapter-02.md", CHAPTER_TWO),
        )

    def test_paths_are_relative_and_posix_on_every_platform(self, corpus: Path) -> None:
        paths = [path for path, _ in FileSystemSource(corpus).read().documents]

        assert "draft/chapter-01.md" in paths
        assert not any("\\" in path for path in paths)

    def test_a_file_that_is_not_text_is_skipped_and_named(self, corpus: Path) -> None:
        (corpus / "cover.png").write_bytes(b"\x89PNG\r\n")
        reading = FileSystemSource(corpus).read()

        assert [path for path, _ in reading.documents] == [
            "cast.md",
            "draft/chapter-01.md",
            "draft/chapter-02.md",
        ]
        assert reading.skipped[0][0] == "cover.png"
        assert "not a text file" in reading.skipped[0][1]

    def test_an_unreadable_file_is_skipped_and_named_rather_than_losing_the_corpus(
        self, corpus: Path
    ) -> None:
        (corpus / "empty.md").write_text("", encoding="utf-8")
        (corpus / "cp1252.md").write_bytes(b"Ada met Bram\xe9 at the gate.\n")
        reading = FileSystemSource(corpus).read()

        why = dict(reading.skipped)
        assert "empty" in why["empty.md"]
        assert "UTF-8" in why["cp1252.md"]
        assert len(reading.documents) == 3

    def test_a_single_file_is_a_corpus_of_one_named_for_the_file(self, corpus: Path) -> None:
        assert FileSystemSource(corpus / "cast.md").read().documents == (("cast.md", CAST),)

    def test_a_corpus_that_is_not_there_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="no such file or folder"):
            FileSystemSource(tmp_path / "absent").read()

    def test_texts_are_the_same_pairs_keyed_by_path(self, corpus: Path) -> None:
        reading = FileSystemSource(corpus).read()
        assert reading.texts == dict(reading.documents)

    def test_the_filesystem_satisfies_the_interface(self, corpus: Path) -> None:
        assert isinstance(FileSystemSource(corpus), Source)
        assert isinstance(InMemorySource("memory:one", ()), Source)


class TestNothingDownstreamNeedsAFilesystem:
    """The refactor's claim, stated as a test.

    Everything after the source is defined on the pairs. A source with no disk under it must
    therefore produce identical results — not merely similar ones, identical, down to the
    revision identifier, because that identifier is a content hash and any divergence in what
    reached the store would move it.
    """

    def test_a_source_with_no_disk_produces_the_same_revision_as_the_folder(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        with Store(tmp_path / "disk.sqlite") as store:
            from_disk = ingest_folder(store, corpus, work_title="W", collection_name="C")

        memory = InMemorySource("memory:corpus", FileSystemSource(corpus).read().documents)
        with Store(tmp_path / "memory.sqlite") as store:
            from_memory = ingest_source(store, memory, work_title="W", collection_name="C")

        assert from_memory.revision_id == from_disk.revision_id
        assert from_memory.sha256 == from_disk.sha256

        def rows(result: object) -> list[tuple[str, str, str]]:
            return [(entry.path, entry.document_id, entry.sha256) for entry in result.documents]

        assert rows(from_memory) == rows(from_disk)

    def test_the_text_stored_is_the_text_the_source_gave(self, tmp_path: Path) -> None:
        memory = InMemorySource("memory:corpus", (("chapter-01.md", CHAPTER_ONE),))
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, memory, work_title="W", collection_name="C")

            assert store.revision_text(result.revision_id) == CHAPTER_ONE

    def test_a_source_that_offers_nothing_readable_says_so(self, tmp_path: Path) -> None:
        memory = InMemorySource("memory:empty", (), (("cover.png", "not a text file"),))
        with (
            Store(tmp_path / "p.sqlite") as store,
            pytest.raises(IngestError, match="no readable text files"),
        ):
            ingest_source(store, memory, work_title="W", collection_name="C")

    def test_what_a_source_could_not_read_reaches_the_result(self, tmp_path: Path) -> None:
        memory = InMemorySource(
            "memory:corpus",
            (("chapter-01.md", CHAPTER_ONE),),
            (("notes.pdf", "not a text file (.pdf)"),),
        )
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, memory, work_title="W", collection_name="C")

        assert result.skipped == (("notes.pdf", "not a text file (.pdf)"),)

    def test_per_document_tracking_works_across_two_readings_of_one_source(
        self, tmp_path: Path
    ) -> None:
        # What 4.15 needs: a second reading of the same root is a second revision, with each
        # document reported against the one before it.
        first = InMemorySource(
            "memory:corpus", (("chapter-01.md", CHAPTER_ONE), ("chapter-02.md", CHAPTER_TWO))
        )
        second = InMemorySource(
            "memory:corpus",
            (("chapter-01.md", CHAPTER_ONE), ("chapter-02.md", "Bram remembered her after all.\n")),
        )
        with Store(tmp_path / "p.sqlite") as store:
            ingest_source(store, first, work_title="W", collection_name="C")
            result = ingest_source(store, second, work_title="W", collection_name="C")

        assert {entry.path: entry.state for entry in result.documents} == {
            "chapter-01.md": "unchanged",
            "chapter-02.md": "changed",
        }

    def test_an_excluded_region_is_dropped_from_a_source_that_is_not_a_file(
        self, tmp_path: Path
    ) -> None:
        # 4.11 on pairs rather than on files. The preface never enters the store, so it never
        # reaches extraction, and the source it came from is irrelevant to that.
        memory = InMemorySource("memory:corpus", (("book.md", PREFACE + NOVEL),))
        plan = {
            "path": "book.md",
            "characters": 0,
            "role": {"value": NARRATIVE, "basis": "confirmed", "settled": True},
            "addressing": {"value": "section", "basis": "D27", "settled": True},
            "revision_of": {"value": None, "basis": "none", "settled": False},
            "regions": [
                {
                    "label": "preface",
                    "role": {"value": EXCLUDED, "basis": "confirmed", "settled": True},
                    "starts_at": 0,
                    "ends_at": None,
                    "begins_with": "",
                    "ends_with": "",
                },
                {
                    "label": "the novel",
                    "role": {"value": NARRATIVE, "basis": "confirmed", "settled": True},
                    "starts_at": 0,
                    "ends_at": None,
                    "begins_with": "It is a truth universally acknowledged.",
                    "ends_with": "",
                },
            ],
        }

        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(memory.root, {"book.md": plan}, "2026-01-01T00:00:00Z")
            result = ingest_source(store, memory, work_title="W", collection_name="C")

            assert result.excluded == ("book.md",)
            assert "Coleridge" not in store.revision_text(result.revision_id)

    def test_a_structure_proposal_reads_the_source_and_not_the_disk(self, corpus: Path) -> None:
        memory = InMemorySource("memory:corpus", FileSystemSource(corpus).read().documents)

        from_disk = as_json(propose_structure(corpus))
        from_memory = as_json(propose_structure(memory))

        assert from_memory["root"] == "memory:corpus"
        assert from_memory["documents"] == from_disk["documents"]

    def test_a_reading_already_taken_is_not_taken_again(self, corpus: Path, tmp_path: Path) -> None:
        """A source is contacted once per ingest, which for a network source is the promise.

        Proven by handing over a reading the source itself would never return: if `ingest_source`
        went back to the source, the corpus it stored would be the folder's three documents
        rather than the one passed in.
        """
        reading = Reading(documents=(("only.md", CAST),))
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(
                store, corpus, work_title="W", collection_name="C", reading=reading
            )

        assert [entry.path for entry in result.documents] == ["only.md"]

"""Multi-file ingest, and the per-file tracking that makes a later diff attributable.

The tests are written against fixture **B** wherever they can be, because its chapters were
authored to differ in exactly one place and a synthetic folder built in a temp directory can
always be made to prove whatever it was built to prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dramatis import ids
from dramatis.ingest import IngestError, ingest_file, ingest_folder
from dramatis.store import Store
from dramatis.text import revision_hash

FIXTURE_B = Path(__file__).resolve().parents[1] / "fixtures" / "b"


def a_folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


class TestAnOlderRevisionKeepsItsText:
    """The defect this bullet exists to fix.

    A document identifier derived from the filename alone means a second ingest of an edited
    file overwrites the content an earlier revision points at. Nothing raises: the older
    revision simply starts reporting text it never held, and its recorded hash stops matching
    what it returns. Every quotation anchored into it then cites a text that does not exist.
    """

    def test_editing_a_file_does_not_rewrite_the_revision_before_it(self, tmp_path: Path) -> None:
        source = tmp_path / "chapter.txt"
        source.write_text("Ada met Bram at the gate.\n", encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_file(store, source, work_title="W", collection_name="C")

        source.write_text("Ada met Cai at the gate instead.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            second = ingest_file(store, source, work_title="W", collection_name="C")

            assert first.revision_id != second.revision_id
            assert store.revision_text(first.revision_id) == "Ada met Bram at the gate.\n"
            assert store.revision_text(second.revision_id) == "Ada met Cai at the gate instead.\n"

    def test_a_revision_still_hashes_to_what_it_returns(self, tmp_path: Path) -> None:
        # The integrity check the silent overwrite used to fail.
        source = tmp_path / "chapter.txt"
        source.write_text("First.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_file(store, source, work_title="W", collection_name="C")

        source.write_text("Second, entirely.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            ingest_file(store, source, work_title="W", collection_name="C")
            revision = store.get_text_revision(first.revision_id)

            assert revision is not None
            assert revision_hash([store.revision_text(first.revision_id)]) == revision.sha256

    def test_each_version_of_a_file_is_its_own_document(self, tmp_path: Path) -> None:
        source = tmp_path / "chapter.txt"
        source.write_text("First.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_file(store, source, work_title="W", collection_name="C")

        source.write_text("Second.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            second = ingest_file(store, source, work_title="W", collection_name="C")
            rows = [row["id"] for row in store.connection.execute("SELECT id FROM documents")]

        assert first.document_id != second.document_id
        assert sorted(rows) == sorted([first.document_id, second.document_id])

    def test_identical_content_is_still_one_document(self, tmp_path: Path) -> None:
        # Idempotence is the promise this module opens with, and content addressing keeps it.
        source = tmp_path / "chapter.txt"
        source.write_text("Unchanged.\n", encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_file(store, source, work_title="W", collection_name="C")
            second = ingest_file(store, source, work_title="W", collection_name="C")
            rows = list(store.connection.execute("SELECT id FROM documents"))

        assert first.document_id == second.document_id
        assert first.revision_id == second.revision_id
        assert len(rows) == 1
        assert second.already_present


class TestIngestingAFolder:
    def test_every_text_file_becomes_a_document_of_one_revision(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")

            assert len(result.documents) == 3
            revision = store.get_text_revision(result.revision_id)
            assert revision is not None
            assert len(revision.document_ids) == 3

    def test_documents_are_ordered_by_path(self, tmp_path: Path) -> None:
        # The order decides the revision hash, so it cannot be the filesystem's opinion.
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")

        assert [entry.path for entry in result.documents] == [
            "chapter-01.md",
            "chapter-02.md",
            "chapter-03.md",
        ]

    def test_the_revision_text_is_every_document_joined_in_that_order(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")
            text = store.revision_text(result.revision_id)

        first = (FIXTURE_B / "draft-1" / "chapter-01.md").read_text(encoding="utf-8")
        last = (FIXTURE_B / "draft-1" / "chapter-03.md").read_text(encoding="utf-8")
        assert text.startswith(first[:40])
        assert text.rstrip().endswith(last.rstrip()[-40:])

    def test_ingesting_the_same_folder_twice_changes_nothing(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")
            second = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")
            revisions = store.list_text_revisions(first.work_id)

        assert first.revision_id == second.revision_id
        assert second.already_present
        assert len(revisions) == 1

    def test_a_nested_folder_is_walked(self, tmp_path: Path) -> None:
        root = a_folder(
            tmp_path / "draft",
            {"one.md": "Ada speaks.\n", "part-two/two.md": "Bram answers.\n"},
        )
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")

        assert [entry.path for entry in result.documents] == ["one.md", "part-two/two.md"]

    def test_a_file_is_refused_with_a_message_naming_the_other_function(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "one.txt"
        source.write_text("Ada.\n", encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store, pytest.raises(IngestError, match="ingest_file"):
            ingest_folder(store, source)

    def test_a_missing_folder_is_a_clean_error(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store, pytest.raises(IngestError, match="no such"):
            ingest_folder(store, tmp_path / "absent")

    def test_a_folder_with_no_text_says_so_rather_than_making_an_empty_revision(
        self, tmp_path: Path
    ) -> None:
        root = a_folder(tmp_path / "draft", {"cover.png": "not really a png"})

        with Store(tmp_path / "p.sqlite") as store, pytest.raises(IngestError, match="no readable"):
            ingest_folder(store, root, work_title="W", collection_name="C")


class TestWhatIsLeftOutIsSaidOutLoud:
    def test_a_file_that_is_not_text_is_skipped_and_named(self, tmp_path: Path) -> None:
        root = a_folder(tmp_path / "draft", {"one.md": "Ada speaks.\n", "cover.png": "binary-ish"})
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")

        assert [entry.path for entry in result.documents] == ["one.md"]
        assert [path for path, _ in result.skipped] == ["cover.png"]
        assert "not a text file" in result.skipped[0][1]

    def test_an_empty_file_is_skipped_and_named(self, tmp_path: Path) -> None:
        # A stub chapter is normal in a draft folder. Failing the whole ingest over one
        # would be worse; dropping it silently would be worse still, because a revision
        # quietly missing a chapter is a graph missing a character with nothing to say why.
        root = a_folder(tmp_path / "draft", {"one.md": "Ada speaks.\n", "two.md": "   \n"})
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")

        assert [entry.path for entry in result.documents] == ["one.md"]
        assert result.skipped[0][0] == "two.md"
        assert "empty" in result.skipped[0][1]

    def test_an_unreadable_file_does_not_discard_the_folder(self, tmp_path: Path) -> None:
        root = tmp_path / "draft"
        root.mkdir()
        (root / "one.md").write_text("Ada speaks.\n", encoding="utf-8", newline="")
        (root / "two.md").write_bytes(b"\xff\xfe not utf-8 at all")

        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")

        assert [entry.path for entry in result.documents] == ["one.md"]
        assert "UTF-8" in result.skipped[0][1]


class TestPerFileRevisionTracking:
    """Fixture B changes exactly one chapter between drafts. The tracking has to agree."""

    def _both_drafts(self, tmp_path: Path):
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(
                store, FIXTURE_B / "draft-1", work_title="Lamplighter", label="First draft"
            )
            second = ingest_folder(
                store, FIXTURE_B / "draft-2", work_title="Lamplighter", label="Second draft"
            )
        return first, second

    def test_the_first_ingest_reports_every_file_as_added(self, tmp_path: Path) -> None:
        first, _ = self._both_drafts(tmp_path)

        assert {entry.state for entry in first.documents} == {"added"}
        assert first.compared_with is None

    def test_the_second_draft_names_the_one_chapter_that_changed(self, tmp_path: Path) -> None:
        # corpus.json: changed_documents ["chapter-03.md"], unchanged the other two.
        _, second = self._both_drafts(tmp_path)

        assert [entry.path for entry in second.of_state("changed")] == ["chapter-03.md"]
        assert [entry.path for entry in second.of_state("unchanged")] == [
            "chapter-01.md",
            "chapter-02.md",
        ]
        assert second.of_state("added") == ()

    def test_it_says_which_revision_it_measured_against(self, tmp_path: Path) -> None:
        first, second = self._both_drafts(tmp_path)
        assert second.compared_with == first.revision_id

    def test_an_unchanged_chapter_is_the_same_document_in_both_revisions(
        self, tmp_path: Path
    ) -> None:
        # This is what makes a diff attributable: the two revisions share the rows nobody
        # touched, so a change can only come from the file that actually changed.
        first, second = self._both_drafts(tmp_path)
        by_path = {entry.path: entry.document_id for entry in first.documents}

        for entry in second.of_state("unchanged"):
            assert entry.document_id == by_path[entry.path]

    def test_a_changed_chapter_is_a_different_document(self, tmp_path: Path) -> None:
        first, second = self._both_drafts(tmp_path)
        by_path = {entry.path: entry.document_id for entry in first.documents}
        changed = second.of_state("changed")[0]

        assert changed.document_id != by_path[changed.path]

    def test_both_drafts_survive_as_distinct_revisions(self, tmp_path: Path) -> None:
        first, second = self._both_drafts(tmp_path)

        with Store(tmp_path / "p.sqlite") as store:
            assert store.revision_text(first.revision_id) != store.revision_text(second.revision_id)
            assert len(store.list_text_revisions(first.work_id)) == 2

    def test_a_new_file_is_added_rather_than_changed(self, tmp_path: Path) -> None:
        root = a_folder(tmp_path / "draft", {"one.md": "Ada speaks.\n"})
        with Store(tmp_path / "p.sqlite") as store:
            ingest_folder(store, root, work_title="W", collection_name="C")
            a_folder(root, {"two.md": "Bram answers.\n"})
            second = ingest_folder(store, root, work_title="W", collection_name="C")

        assert [entry.path for entry in second.of_state("added")] == ["two.md"]
        assert [entry.path for entry in second.of_state("unchanged")] == ["one.md"]

    def test_a_deleted_file_simply_leaves_the_revision(self, tmp_path: Path) -> None:
        # Removal is a property of the revision, not of any file in it: the new revision
        # holds two documents where the old held three, and the old still holds three.
        root = a_folder(
            tmp_path / "draft", {"one.md": "Ada.\n", "two.md": "Bram.\n", "three.md": "Cai.\n"}
        )
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(store, root, work_title="W", collection_name="C")
            (root / "two.md").unlink()
            second = ingest_folder(store, root, work_title="W", collection_name="C")

            assert len(second.documents) == 2
            assert len(store.get_text_revision(first.revision_id).document_ids) == 3


class TestTheSummaryAUserReads:
    def test_it_counts_the_files_by_state(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")
            second = ingest_folder(store, FIXTURE_B / "draft-2", work_title="Lamplighter")

        assert "1 changed" in second.summary
        assert "2 unchanged" in second.summary
        assert second.revision_id in second.summary

    def test_it_is_ascii_so_a_legacy_console_does_not_render_corruption(
        self, tmp_path: Path
    ) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, FIXTURE_B / "draft-1", work_title="Lamplighter")

        result.summary.encode("ascii")


class TestDocumentIdentifiers:
    def test_the_same_bytes_always_give_the_same_identifier(self) -> None:
        assert ids.document_id("chapter-03", "abc123def456789") == ids.document_id(
            "chapter-03", "abc123def456789"
        )

    def test_different_bytes_give_different_identifiers(self) -> None:
        assert ids.document_id("chapter-03", "aaaa") != ids.document_id("chapter-03", "bbbb")

    def test_the_name_is_still_legible_in_the_identifier(self) -> None:
        # An opaque hash would make a stored document unreadable in a snapshot document,
        # where a human is the one tracing an evidence locator back to its file.
        assert ids.document_id("chapter-03", "abcdef123456").startswith("doc:chapter-03-")

    def test_a_document_with_no_content_hash_keeps_the_old_shape(self) -> None:
        assert ids.document_id("chapter-03") == "doc:chapter-03"


class TestEvidenceKnowsWhichDocumentItCameFrom:
    """A revision of a folder is many documents concatenated.

    Until a revision could hold more than one, naming the first document for every
    quotation was indistinguishable from naming the right one. With shape **B** it is the
    difference between citing chapter three and citing chapter one.
    """

    def _analysed(self, tmp_path: Path):
        import json

        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        store = Store(tmp_path / "p.sqlite").open()
        ingested = ingest_folder(store, FIXTURE_B / "draft-1", work_title="L", collection_name="S")

        quotations = []
        for entry in ingested.documents:
            body = store.get_document(entry.document_id).content
            longest = max((line.strip() for line in body.split("\n")), key=len)
            quotations.append((entry.document_id, longest[:70]))

        names = ("Auber Vance", "Idris Kell")
        reply = json.dumps(
            {
                "characters": [{"name": n, "aliases": [], "kind": "person"} for n in names],
                "interactions": [
                    {"participants": list(names), "quotation": quotation, "note": ""}
                    for _, quotation in quotations
                ],
            }
        )
        grouping = json.dumps(
            {
                "groups": [
                    {
                        "canonical_name": n,
                        "forms": [n],
                        "kind": "person",
                        "same_as_registered": "",
                    }
                    for n in names
                ]
            }
        )
        result = analyse(store, ingested.revision_id, ScriptedProvider([reply, grouping]))
        return store, ingested, result

    def test_each_quotation_is_attributed_to_its_own_chapter(self, tmp_path: Path) -> None:
        store, ingested, result = self._analysed(tmp_path)
        try:
            evidence = result.snapshot.document["relations"][0]["evidence"]
            attributed = {piece["locator"]["document_id"] for piece in evidence}

            assert attributed == {entry.document_id for entry in ingested.documents}
        finally:
            store.close()

    def test_the_snapshot_carries_every_document_of_the_revision(self, tmp_path: Path) -> None:
        store, ingested, result = self._analysed(tmp_path)
        try:
            carried = [entry["id"] for entry in result.snapshot.document["documents"]]
            assert carried == [entry.document_id for entry in ingested.documents]
        finally:
            store.close()


class TestRevisionDocumentSpans:
    def test_the_spans_tile_the_text_the_revision_returns(self, tmp_path: Path) -> None:
        # The span map and the concatenation have to agree, which is why they live together.
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(
                store, FIXTURE_B / "draft-1", work_title="L", collection_name="S"
            )
            text = store.revision_text(result.revision_id)
            spans = store.revision_document_spans(result.revision_id)

            assert spans[0][0] == 0
            assert spans[-1][1] == len(text)
            for (_, end, _), (start, _, _) in zip(spans[:-1], spans[1:], strict=True):
                assert end == start, "the spans must leave no gap, or an offset falls nowhere"

            for start, end, document_id in spans:
                assert text[start:end] == store.get_document(document_id).content

    def test_a_single_document_revision_is_one_span(self, tmp_path: Path) -> None:
        source = tmp_path / "one.txt"
        source.write_text("Ada speaks.\n", encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_file(store, source, work_title="W", collection_name="C")
            spans = store.revision_document_spans(result.revision_id)

        assert len(spans) == 1
        assert spans[0][0] == 0

    def test_a_revision_that_does_not_exist_has_no_spans(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            assert store.revision_document_spans("rev:absent") == []

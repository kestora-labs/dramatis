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
        # Idempotence is the promise this module opens with: the same bytes at the same path
        # are the same document, however often the folder is read.
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


class TestTwoFilesMayHoldTheSameText:
    """The defect D40 exists to fix.

    Content-addressed identifiers made two byte-identical files one document, and a document
    can sit at only one place in one revision. That is not a rare collision: a drafts folder
    is mostly chapters nobody touched between revisions, so it is the *usual* shape. Ingesting
    the folder that holds both drafts raised on `revision_documents`' composite key — and the
    key was the guard, not the fault. Without it the revision would simply have lost a chapter.
    """

    def test_two_identical_files_under_different_paths_are_two_documents(
        self, tmp_path: Path
    ) -> None:
        root = a_folder(
            tmp_path / "book",
            {"draft-1/chapter-01.md": "Ada waited.\n", "draft-2/chapter-01.md": "Ada waited.\n"},
        )
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")
            revision = store.get_text_revision(result.revision_id)

            assert [entry.path for entry in result.documents] == [
                "draft-1/chapter-01.md",
                "draft-2/chapter-01.md",
            ]
            assert len({entry.document_id for entry in result.documents}) == 2
            assert revision is not None
            assert len(revision.document_ids) == 2

    def test_each_keeps_its_own_path(self, tmp_path: Path) -> None:
        # One row for both would carry whichever path was written last, so per-file tracking
        # would stop being able to say which chapter changed.
        root = a_folder(
            tmp_path / "book",
            {"draft-1/chapter-01.md": "Ada waited.\n", "draft-2/chapter-01.md": "Ada waited.\n"},
        )
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")
            stored = [store.get_document(entry.document_id) for entry in result.documents]

            assert [document.path for document in stored] == [
                "draft-1/chapter-01.md",
                "draft-2/chapter-01.md",
            ]

    def test_the_revision_holds_the_text_twice(self, tmp_path: Path) -> None:
        # The work contains both copies, so the revision's text and hash must too. A shared
        # row would have silently dropped one, and the graph would be missing a chapter.
        root = a_folder(
            tmp_path / "book",
            {"draft-1/chapter-01.md": "Ada waited.\n", "draft-2/chapter-01.md": "Ada waited.\n"},
        )
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="W", collection_name="C")

            assert store.revision_text(result.revision_id) == "Ada waited.\nAda waited.\n"
            assert len(store.revision_document_spans(result.revision_id)) == 2

    def test_fixture_b_ingests_whole(self, tmp_path: Path) -> None:
        # The report: `dramatis ingest fixtures/b`. Chapters 1 and 2 are untouched between
        # the two drafts and so are byte-identical; chapter 3 is the rewritten one.
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, FIXTURE_B, work_title="Lamplighter")

            assert len({entry.document_id for entry in result.documents}) == len(result.documents)
            assert {"draft-1/chapter-01.md", "draft-2/chapter-01.md"} <= {
                entry.path for entry in result.documents
            }

    def test_the_two_drafts_ingested_separately_still_share_a_row(self, tmp_path: Path) -> None:
        # D32's property, and what keeps it: the path is *relative to the folder ingested*, so
        # an untouched chapter is `chapter-01.md` in both drafts and keeps one identifier.
        # Absolute paths would mint a second row and per-file tracking would report a change
        # where the fixture says there is none.
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(store, FIXTURE_B / "draft-1", work_title="L")
            second = ingest_folder(store, FIXTURE_B / "draft-2", work_title="L")
            rows = store.count("documents")

        by_path = {entry.path: entry for entry in second.documents}
        assert by_path["chapter-01.md"].state == "unchanged"
        assert by_path["chapter-03.md"].state == "changed"
        assert (
            by_path["chapter-01.md"].document_id
            == {entry.path: entry for entry in first.documents}["chapter-01.md"].document_id
        )
        assert rows == 4, "three chapters, one of them rewritten"


class TestDocumentIdentifiers:
    def test_the_same_bytes_at_the_same_path_always_give_the_same_identifier(self) -> None:
        assert ids.document_id("chapter-03.md", "abc123def456789") == ids.document_id(
            "chapter-03.md", "abc123def456789"
        )

    def test_different_bytes_give_different_identifiers(self) -> None:
        assert ids.document_id("chapter-03.md", "aaaa") != ids.document_id("chapter-03.md", "bbbb")

    def test_the_same_bytes_at_different_paths_give_different_identifiers(self) -> None:
        # The other half of identity, and the one D40 added: two documents may legitimately
        # hold the same content, which is the ordinary state of a chapter nobody touched.
        assert ids.document_id("draft-1/chapter-01.md", "aaaa") != ids.document_id(
            "draft-2/chapter-01.md", "aaaa"
        )

    def test_the_path_is_still_legible_in_the_identifier(self) -> None:
        # An opaque hash would make a stored document unreadable in a snapshot document,
        # where a human is the one tracing an evidence locator back to its file.
        assert ids.document_id("draft-2/chapter-03.md", "abcdef123456").startswith(
            "doc:draft-2-chapter-03-md-"
        )

    def test_uniqueness_does_not_rest_on_the_slug_being_lossless(self) -> None:
        # `slugify` collapses separators and truncates at MAX_SLUG_LENGTH, so two different
        # paths can reduce to one token. The hash covers the path, so they still differ.
        stub = "TBD"
        deep = "drafts/" + "the-long-winter-" * 5
        first, second = f"{deep}/chapter-17.md", f"{deep}/chapter-18.md"

        assert ids.slugify(first) == ids.slugify(second), "the premise: the slugs collide"
        assert ids.document_id(first, stub) != ids.document_id(second, stub)


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


class TestRevisionsAreListedInTheOrderTheyWereMade:
    """Two revisions ingested in the same second are ordinary — a folder of drafts read one
    after another. A revision identifier is a content hash, so breaking the tie with it
    orders the drafts by hashing, and 3.2's lineage is the first thing that shows the order
    to a reader."""

    def test_two_drafts_ingested_together_keep_their_order(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(
                store,
                FIXTURE_B / "draft-1",
                work_title="L",
                collection_name="S",
                now="2026-01-01T00:00:00Z",
            )
            second = ingest_folder(
                store, FIXTURE_B / "draft-2", work_title="L", now="2026-01-01T00:00:00Z"
            )
            listed = [revision.id for revision in store.list_text_revisions(first.work_id)]

        assert listed == [first.revision_id, second.revision_id]

    def test_the_later_timestamp_still_wins_when_they_differ(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            later = ingest_folder(
                store,
                FIXTURE_B / "draft-2",
                work_title="L",
                collection_name="S",
                now="2026-06-01T00:00:00Z",
            )
            earlier = ingest_folder(
                store, FIXTURE_B / "draft-1", work_title="L", now="2026-01-01T00:00:00Z"
            )
            listed = [revision.id for revision in store.list_text_revisions(later.work_id)]

        assert listed == [earlier.revision_id, later.revision_id]


class TestSnapshotsAreListedInTheOrderTheyWereMade:
    """3.4 reads this order to decide which of two snapshots a diff runs *from*.

    A snapshot identifier is a content hash, so breaking a `created_at` tie with it orders
    two snapshots written in the same second by hashing — and a diff run backwards reports
    every strengthening as a weakening.
    """

    def test_two_snapshots_written_together_keep_their_order(self, tmp_path: Path) -> None:
        import json as _json

        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        def script(store, revision):
            text = store.revision_text(revision)
            line = max((piece.strip() for piece in text.split("\n")), key=len)[:70]
            names = ("Auber Vance", "Idris Kell")
            reply = _json.dumps(
                {
                    "characters": [{"name": n, "aliases": [], "kind": "person"} for n in names],
                    "interactions": [{"participants": list(names), "quotation": line, "note": ""}],
                }
            )
            grouping = _json.dumps(
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
            return [reply, grouping]

        at = "2026-01-01T00:00:00+00:00"
        with Store(tmp_path / "p.sqlite") as store:
            one = ingest_folder(store, FIXTURE_B / "draft-1", work_title="L", collection_name="S")
            two = ingest_folder(store, FIXTURE_B / "draft-2", work_title="L")

            first = analyse(
                store, one.revision_id, ScriptedProvider(script(store, one.revision_id)), now=at
            )
            second = analyse(
                store, two.revision_id, ScriptedProvider(script(store, two.revision_id)), now=at
            )

            listed = [snapshot.id for snapshot in store.list_snapshots(one.work_id)]

        assert listed == [first.snapshot.id, second.snapshot.id]

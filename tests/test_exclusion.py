"""Dropping a region a person confirmed as not part of the work (4.11).

D31's measurement is the reason this exists: of 102 characters in the first full run of
*Pride and Prejudice*, 38 came only from the 1894 edition's critical preface — Coleridge,
Whitman, Scott, characters from Austen's *other* novels — none of them in the book. This is
the machinery that lets a person exclude that preface so the cast is the book's.

The exclusion is mechanical, not a prompt instruction (the brainstorm behind D47): the text
is not sent, rather than sent with an ask to ignore it. So the proof is direct — the text a
later analysis would read no longer contains the preface — and needs no model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.ingest import IngestError, ingest_file, ingest_folder, kept_text
from dramatis.store import EXCLUDED, NARRATIVE, REFERENCE, Store
from dramatis.structure import propose_structure, save

FIXTURE_A = (
    Path(__file__).resolve().parents[1] / "fixtures" / "a" / "source" / "pride-and-prejudice.txt"
)

PREFACE = (
    "PREFACE\n\nThis edition is introduced by a critic who admired Coleridge and Whitman "
    "and could not resist saying so at length.\n\n"
)
NOVEL = (
    "It is a truth universally acknowledged, that a single man in possession of a good "
    "fortune, must be in want of a wife.\n\nAda met Bram at the gate.\n"
)
APPENDIX = "\nAPPENDIX\n\nA note on the text, by a later and equally garrulous hand.\n"


def a_region(role: str, *, begins: str = "", ends: str = "") -> dict:
    return {
        "label": role,
        "role": {"value": role, "basis": "confirmed", "settled": True},
        "starts_at": 0,
        "ends_at": None,
        "begins_with": begins,
        "ends_with": ends,
    }


def a_plan(path: str, regions: list[dict], role: str = NARRATIVE) -> dict:
    return {
        "path": path,
        "characters": 0,
        "role": {"value": role, "basis": "confirmed", "settled": True},
        "addressing": {"value": "section", "basis": "D27", "settled": True},
        "revision_of": {"value": None, "basis": "none", "settled": False},
        "regions": regions,
    }


class TestKeptText:
    """The helper, in isolation. `plan` is the JSON `structure.as_json` writes."""

    def test_no_excluded_region_leaves_the_text_alone(self) -> None:
        plan = a_plan("x.md", [a_region(NARRATIVE)])
        kept, note = kept_text(PREFACE + NOVEL, plan)

        assert kept == PREFACE + NOVEL
        assert note is None

    def test_a_preface_before_the_narrative_is_dropped(self) -> None:
        plan = a_plan(
            "x.md",
            [
                a_region(EXCLUDED),
                a_region(NARRATIVE, begins="It is a truth universally acknowledged"),
            ],
        )
        kept, note = kept_text(PREFACE + NOVEL, plan)

        assert note is None
        assert kept.startswith("It is a truth")
        assert "Coleridge" not in kept

    def test_an_appendix_after_the_narrative_is_dropped(self) -> None:
        plan = a_plan(
            "x.md",
            [
                a_region(NARRATIVE, ends="Ada met Bram at the gate."),
                a_region(EXCLUDED),
            ],
        )
        kept, note = kept_text(NOVEL + APPENDIX, plan)

        assert note is None
        assert kept.endswith("Ada met Bram at the gate.\n") or kept.endswith("gate.")
        assert "APPENDIX" not in kept

    def test_both_ends_can_go_at_once(self) -> None:
        plan = a_plan(
            "x.md",
            [
                a_region(EXCLUDED),
                a_region(
                    NARRATIVE,
                    begins="It is a truth universally acknowledged",
                    ends="Ada met Bram at the gate.",
                ),
                a_region(EXCLUDED),
            ],
        )
        kept, note = kept_text(PREFACE + NOVEL + APPENDIX, plan)

        assert note is None
        assert "Coleridge" not in kept
        assert "APPENDIX" not in kept
        assert kept.startswith("It is a truth")

    def test_a_boundary_reflowed_since_confirmation_still_anchors(self) -> None:
        # The file was hard-wrapped after the region was confirmed; whitespace-flexible
        # matching finds the boundary anyway, which is the whole reason it is not an offset.
        wrapped = PREFACE + "It is a truth\nuniversally    acknowledged, that a single man.\n"
        plan = a_plan(
            "x.md",
            [
                a_region(EXCLUDED),
                a_region(NARRATIVE, begins="It is a truth universally acknowledged"),
            ],
        )
        kept, note = kept_text(wrapped, plan)

        assert note is None
        assert kept.startswith("It is a truth")

    def test_a_boundary_that_cannot_be_found_drops_nothing_and_says_why(self) -> None:
        plan = a_plan(
            "x.md",
            [a_region(EXCLUDED), a_region(NARRATIVE, begins="Call me Ishmael")],
        )
        kept, note = kept_text(PREFACE + NOVEL, plan)

        assert kept == PREFACE + NOVEL
        assert note is not None and "not in the document" in note

    def test_an_excluded_region_with_no_boundary_anywhere_drops_nothing(self) -> None:
        plan = a_plan("x.md", [a_region(EXCLUDED), a_region(NARRATIVE)])
        kept, note = kept_text(PREFACE + NOVEL, plan)

        assert kept == PREFACE + NOVEL
        assert note is not None


class TestIngestFileExclusion:
    def _confirm_exclusion(self, store: Store, path: Path, begins: str) -> None:
        plan = a_plan(
            path.name,
            [a_region(EXCLUDED), a_region(NARRATIVE, begins=begins)],
        )
        store.save_structure_map(str(path.resolve()), {path.name: plan}, "2026-01-01T00:00:00Z")

    def test_the_preface_never_enters_the_store(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(PREFACE + NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            self._confirm_exclusion(store, source, "It is a truth universally acknowledged")
            result = ingest_file(store, source, work_title="A Novel", collection_name="A Set")
            stored = store.get_document(result.document_id).content

        assert result.excluded is True
        assert stored.startswith("It is a truth")
        assert "Coleridge" not in stored

    def test_the_revision_a_later_analysis_reads_excludes_it(self, tmp_path: Path) -> None:
        # analyse() feeds revision_text(roles=[NARRATIVE]) to extraction. If the preface is
        # gone from there, the model never sees it, which is the whole mechanism.
        source = tmp_path / "novel.txt"
        source.write_text(PREFACE + NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            self._confirm_exclusion(store, source, "It is a truth universally acknowledged")
            result = ingest_file(store, source, work_title="A Novel", collection_name="A Set")
            narrative = store.revision_text(result.revision_id, roles=[NARRATIVE])

        assert "Coleridge" not in narrative
        assert "Ada met Bram" in narrative

    def test_no_confirmed_map_leaves_the_file_whole(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(PREFACE + NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_file(store, source, work_title="A Novel", collection_name="A Set")
            stored = store.get_document(result.document_id).content

        assert result.excluded is False
        assert "Coleridge" in stored

    def test_an_unfindable_boundary_is_refused_not_ignored(self, tmp_path: Path) -> None:
        # Silently keeping the preface would produce exactly the polluted cast the exclusion
        # was for, so it fails loudly instead.
        source = tmp_path / "novel.txt"
        source.write_text(PREFACE + NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            self._confirm_exclusion(store, source, "a sentence that is nowhere in the file")

            with pytest.raises(IngestError, match="not in the document"):
                ingest_file(store, source, work_title="A Novel", collection_name="A Set")

    def test_the_summary_says_a_region_was_excluded(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(PREFACE + NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            self._confirm_exclusion(store, source, "It is a truth universally acknowledged")
            summary = ingest_file(
                store, source, work_title="A Novel", collection_name="A Set"
            ).summary

        assert "region was excluded" in summary
        summary.encode("ascii")


class TestADocumentThatIsNoPartOfTheWork:
    """**W1**: a third answer, for a file that is in the folder and is not the work.

    Found by a real corpus. A Drive folder of comic-book development held a to-do roadmap, a
    script format spec, a production pipeline spec and five sheets of image-generation prompts
    — none of them narrative, none of them a character bible, and no way to say so. Calling
    them reference material feeds production vocabulary into the cast.

    The mechanism is **D47**'s, one level up: the text is not stored, rather than stored and
    filtered by everything downstream. What is excluded is not a third *kind* of document; it
    is the absence of one, which is why `documents.role` still takes exactly two values.
    """

    def a_corpus(self, root: Path) -> Path:
        root.mkdir(exist_ok=True)
        (root / "chapter.md").write_text(NOVEL, encoding="utf-8", newline="")
        (root / "cast.md").write_text("Ada is Bram's sister.\n", encoding="utf-8", newline="")
        (root / "TODO.md").write_text(
            "Redraw page 4. Chase the letterer. Ask Bram about the cover.\n",
            encoding="utf-8",
            newline="",
        )
        return root

    def a_map(self, store: Store, root: Path, **roles: str) -> None:
        plans = {path: a_plan(path, [a_region(role)], role) for path, role in roles.items()}
        store.save_structure_map(str(root.resolve()), plans, "2026-01-01T00:00:00Z")

    def test_an_excluded_document_is_left_out_of_the_revision(self, tmp_path: Path) -> None:
        root = self.a_corpus(tmp_path / "corpus")
        with Store(tmp_path / "p.sqlite") as store:
            self.a_map(
                store,
                root,
                **{"chapter.md": NARRATIVE, "cast.md": REFERENCE, "TODO.md": EXCLUDED},
            )
            result = ingest_folder(store, root, work_title="W")

            assert [entry.path for entry in result.documents] == ["cast.md", "chapter.md"]
            assert result.omitted == ("TODO.md",)

    def test_its_text_never_enters_the_store(self, tmp_path: Path) -> None:
        # The point of dropping rather than filtering: text that is not stored cannot reach a
        # model, whatever any later stage forgets.
        root = self.a_corpus(tmp_path / "corpus")
        with Store(tmp_path / "p.sqlite") as store:
            self.a_map(
                store,
                root,
                **{"chapter.md": NARRATIVE, "cast.md": REFERENCE, "TODO.md": EXCLUDED},
            )
            result = ingest_folder(store, root, work_title="W")

            assert "Chase the letterer" not in store.revision_text(result.revision_id)

    def test_excluding_a_document_changes_the_revision(self, tmp_path: Path) -> None:
        # It is a different corpus, so it is a different revision. Anything else would let two
        # different studies share an identifier.
        root = self.a_corpus(tmp_path / "corpus")
        with Store(tmp_path / "p.sqlite") as store:
            whole = ingest_folder(store, root, work_title="W")
            self.a_map(
                store,
                root,
                **{"chapter.md": NARRATIVE, "cast.md": REFERENCE, "TODO.md": EXCLUDED},
            )
            without = ingest_folder(store, root, work_title="W")

            assert whole.revision_id != without.revision_id

    def test_the_summary_says_it_out_loud(self, tmp_path: Path) -> None:
        root = self.a_corpus(tmp_path / "corpus")
        with Store(tmp_path / "p.sqlite") as store:
            self.a_map(
                store,
                root,
                **{"chapter.md": NARRATIVE, "cast.md": REFERENCE, "TODO.md": EXCLUDED},
            )
            result = ingest_folder(store, root, work_title="W")

            assert "1 left out as no part of the work" in result.summary

    def test_excluding_everything_is_refused_rather_than_producing_an_empty_work(
        self, tmp_path: Path
    ) -> None:
        root = self.a_corpus(tmp_path / "corpus")
        with Store(tmp_path / "p.sqlite") as store:
            self.a_map(
                store,
                root,
                **{"chapter.md": EXCLUDED, "cast.md": EXCLUDED, "TODO.md": EXCLUDED},
            )
            with pytest.raises(IngestError, match="nothing left to study"):
                ingest_folder(store, root, work_title="W")

    def test_a_single_file_corpus_cannot_exclude_itself(self, tmp_path: Path) -> None:
        source = tmp_path / "notes.md"
        source.write_text(NOVEL, encoding="utf-8", newline="")
        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(
                str(source.resolve()),
                {"notes.md": a_plan("notes.md", [a_region(EXCLUDED)], EXCLUDED)},
                "2026-01-01T00:00:00Z",
            )
            with pytest.raises(IngestError, match="nothing left to study"):
                ingest_file(store, source, work_title="W")

    def test_omitted_is_not_the_same_thing_as_excluded(self, tmp_path: Path) -> None:
        """Three different facts, and a person needs them apart.

        `skipped` could not be read, `excluded` is here with a span removed, `omitted` was read
        and deliberately not kept.
        """
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "book.md").write_text(PREFACE + NOVEL, encoding="utf-8", newline="")
        (root / "TODO.md").write_text("Chase the letterer.\n", encoding="utf-8", newline="")
        (root / "cover.png").write_bytes(b"\x89PNG\r\n")

        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(
                str(root.resolve()),
                {
                    "book.md": a_plan(
                        "book.md",
                        [
                            a_region(EXCLUDED),
                            a_region(NARRATIVE, begins="It is a truth universally acknowledged"),
                        ],
                    ),
                    "TODO.md": a_plan("TODO.md", [a_region(EXCLUDED)], EXCLUDED),
                },
                "2026-01-01T00:00:00Z",
            )
            result = ingest_folder(store, root, work_title="W")

        assert result.excluded == ("book.md",)
        assert result.omitted == ("TODO.md",)
        assert [path for path, _ in result.skipped] == ["cover.png"]


class TestIngestFolderExclusion:
    def test_a_preface_bound_into_one_chapter_is_dropped(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "chapter-01.md").write_text(PREFACE + NOVEL, encoding="utf-8", newline="")
        (root / "chapter-02.md").write_text(
            "Cai waited by the water.\n", encoding="utf-8", newline=""
        )

        plan = a_plan(
            "chapter-01.md",
            [
                a_region(EXCLUDED),
                a_region(NARRATIVE, begins="It is a truth universally acknowledged"),
            ],
        )
        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(
                str(root.resolve()), {"chapter-01.md": plan}, "2026-01-01T00:00:00Z"
            )
            result = ingest_folder(store, root, work_title="A Serial")
            narrative = store.revision_text(result.revision_id, roles=[NARRATIVE])

        assert result.excluded == ("chapter-01.md",)
        assert "Coleridge" not in narrative
        assert "Cai waited" in narrative

    def test_a_folder_with_no_exclusions_reports_none(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "one.md").write_text(NOVEL, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_folder(store, root, work_title="A Serial")

        assert result.excluded == ()


class TestFixtureAsPreface:
    """The case D31 measured: the critical preface bound into the single Gutenberg file."""

    # Verbatim from the fixture, comma and all: the source reads "good fortune must" with no
    # comma, and whitespace-flexible matching forgives the line break but not a changed word.
    NARRATIVE_START = (
        "It is a truth universally acknowledged, that a single man in possession of a good "
        "fortune must be in want of a wife."
    )

    def test_the_preface_of_pride_and_prejudice_is_excluded(self, tmp_path: Path) -> None:
        if not FIXTURE_A.is_file():
            pytest.skip("fixture A source is not present")

        # A single file is its own structure-map root. Confirm a preface region before the
        # narrative, ingest, and the 34k-character preface — Coleridge, Whitman and all —
        # never enters the store.
        plan = a_plan(
            FIXTURE_A.name,
            [a_region(EXCLUDED), a_region(NARRATIVE, begins=self.NARRATIVE_START)],
        )
        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(
                str(FIXTURE_A.resolve()), {FIXTURE_A.name: plan}, "2026-01-01T00:00:00Z"
            )
            result = ingest_file(store, FIXTURE_A, work_title="Pride and Prejudice")
            stored = store.get_document(result.document_id).content

        assert result.excluded is True
        assert stored.lstrip().startswith("It is a truth universally acknowledged")
        assert "Coleridge" not in stored
        assert "Whitman" not in stored
        # The novel itself is intact.
        assert "My dear Mr. Bennet" in stored

    def test_the_map_can_be_proposed_for_the_single_file(self, tmp_path: Path) -> None:
        # propose_structure now accepts a file, so the browser (4.9) can offer regions for a
        # single chosen novel without a folder.
        if not FIXTURE_A.is_file():
            pytest.skip("fixture A source is not present")

        structure = propose_structure(FIXTURE_A)

        assert [plan.path for plan in structure.documents] == [FIXTURE_A.name]

    def test_a_confirmed_single_file_map_round_trips_through_save(self, tmp_path: Path) -> None:
        if not FIXTURE_A.is_file():
            pytest.skip("fixture A source is not present")

        from dramatis.structure import confirm

        with Store(tmp_path / "p.sqlite") as store:
            structure = propose_structure(FIXTURE_A)
            save(confirm(structure, {FIXTURE_A.name: NARRATIVE}), store)
            saved = store.structure_map(str(FIXTURE_A.resolve()))

        assert FIXTURE_A.name in saved


def _no_preface(text: str) -> bool:
    return "Coleridge" not in text and "Whitman" not in text


def test_the_helper_is_pure_json_in_json_out() -> None:
    # kept_text takes the plan as the store hands it back, so a round-trip through JSON does
    # not change its behaviour — the browser and the CLI feed it the same shape.
    plan = a_plan("x.md", [a_region(EXCLUDED), a_region(NARRATIVE, begins="It is a truth")])
    kept, _ = kept_text(PREFACE + NOVEL, json.loads(json.dumps(plan)))

    assert _no_preface(kept)

"""Proposing what a folder holds.

Fixture **C** exists to catch a structure inference that reads filing conventions: its
reference material sits in `series-bible/`, its narrative in `transmissions/`, and its README
says plainly that *"if any of this leaks into the core as a special case, Invariant 1 or the
'not tied to one author's method' non-goal has been broken."* Several tests here are that
trap, sprung deliberately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dramatis.ingest import IngestError
from dramatis.structure import UNKNOWN, as_json, propose_structure

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def a_folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


class TestNoFilingConventionIsRead:
    """The heart of it. Every assertion here is a special case that must not exist."""

    def test_a_folder_named_series_bible_earns_no_role(self) -> None:
        structure = propose_structure(FIXTURES / "c")
        bible = structure.plan_for("series-bible/ada-mbeki.md")

        assert bible is not None
        assert bible.role.value == UNKNOWN

    def test_a_folder_named_transmissions_earns_no_role(self) -> None:
        structure = propose_structure(FIXTURES / "c")
        narrative = structure.plan_for("transmissions/t01.md")

        assert narrative is not None
        assert narrative.role.value == UNKNOWN

    def test_no_document_anywhere_is_given_a_role(self) -> None:
        # Deciding this means reading what a document says, which is 4.2's model.
        for fixture in ("b", "c"):
            for plan in propose_structure(FIXTURES / fixture).documents:
                assert plan.role.value == UNKNOWN, plan.path

    def test_the_refusal_says_why_and_names_where_it_is_answered(self) -> None:
        plan = propose_structure(FIXTURES / "c").documents[0]

        assert "4.2" in plan.role.basis
        assert "folder named" in plan.role.basis

    def test_numbered_units_are_not_read_as_revisions_of_each_other(self) -> None:
        # t01, t02, t03 are three transmissions, not three drafts of one.
        structure = propose_structure(FIXTURES / "c")
        assert structure.revisions == ()

    def test_it_says_out_loud_that_it_found_no_revisions(self) -> None:
        # Fixture C keeps revisions in YAML frontmatter. Silence would read as "there are
        # none" rather than "none are visible from here".
        structure = propose_structure(FIXTURES / "c")

        assert any("inside the files" in note for note in structure.notes)


class TestRevisionsAreProposedFromTheFilename:
    def test_every_chapter_of_the_second_draft_revises_the_first(self) -> None:
        structure = propose_structure(FIXTURES / "b")
        revisions = dict(structure.revisions)

        for chapter in ("chapter-01.md", "chapter-02.md", "chapter-03.md"):
            assert revisions[f"draft-2/{chapter}"] == f"draft-1/{chapter}"

    def test_the_rewritten_chapter_is_proposed_like_the_others(self) -> None:
        """The one that matters. `corpus.json` names chapter-03 as the changed document, and
        a proposal that dropped it would miss the only revision the fixture is about."""
        structure = propose_structure(FIXTURES / "b")
        plan = structure.plan_for("draft-2/chapter-03.md")

        assert plan is not None
        assert plan.revision_of.value == "draft-1/chapter-03.md"

    def test_the_basis_reports_how_alike_the_two_are(self) -> None:
        structure = propose_structure(FIXTURES / "b")
        untouched = structure.plan_for("draft-2/chapter-01.md")
        rewritten = structure.plan_for("draft-2/chapter-03.md")

        assert "100%" in untouched.revision_of.basis
        assert "100%" not in rewritten.revision_of.basis

    def test_the_earlier_document_is_proposed_as_the_original(self) -> None:
        structure = propose_structure(FIXTURES / "b")

        assert structure.plan_for("draft-1/chapter-01.md").revision_of.value is None
        assert structure.plan_for("draft-2/chapter-01.md").revision_of.value is not None

    def test_a_document_with_no_namesake_says_so(self) -> None:
        structure = propose_structure(FIXTURES / "b")
        cast = structure.plan_for("cast.md")

        assert cast.revision_of.value is None
        assert "cast.md" in cast.revision_of.basis

    def test_two_unrelated_files_sharing_a_name_are_still_proposed_but_flagged(
        self, tmp_path: Path
    ) -> None:
        # A proposal, not a conclusion: the person confirming is told the two have almost
        # nothing in common and can say no.
        root = a_folder(
            tmp_path / "corpus",
            {
                "one/notes.md": "Ada met Bram at the gate and neither of them spoke.\n",
                "two/notes.md": "Entirely different words about a completely other subject.\n",
            },
        )
        structure = propose_structure(root)
        plan = structure.plan_for("two/notes.md")

        assert plan.revision_of.value == "one/notes.md"
        assert "rewrite" in plan.revision_of.basis or "unrelated" in plan.revision_of.basis


class TestAddressing:
    def test_every_document_is_addressed_the_only_way_that_can_be_reproduced(self) -> None:
        for plan in propose_structure(FIXTURES / "c").documents:
            assert plan.addressing.value == "section"
            assert plan.addressing.settled

    def test_the_basis_names_the_decision_it_rests_on(self) -> None:
        plan = propose_structure(FIXTURES / "c").documents[0]
        assert "D27" in plan.addressing.basis


class TestRegions:
    def test_a_document_starts_as_one_region_covering_all_of_it(self) -> None:
        # D31 widened the map to hold regions. Finding where a preface ends means reading the
        # text, so the honest floor is a single region claiming only that the document exists.
        plan = propose_structure(FIXTURES / "b").plan_for("cast.md")

        assert len(plan.regions) == 1
        assert plan.regions[0].starts_at == 0
        assert plan.regions[0].ends_at == plan.characters

    def test_a_region_carries_its_own_role_proposal(self) -> None:
        plan = propose_structure(FIXTURES / "b").plan_for("cast.md")
        assert plan.regions[0].role.value == UNKNOWN


class TestWhatIsLeftOut:
    def test_a_file_that_is_not_text_is_skipped_and_named(self, tmp_path: Path) -> None:
        root = a_folder(tmp_path / "corpus", {"one.md": "Ada speaks.\n", "cover.png": "binary"})
        structure = propose_structure(root)

        assert [plan.path for plan in structure.documents] == ["one.md"]
        assert structure.skipped[0][0] == "cover.png"

    def test_an_empty_folder_says_so_rather_than_proposing_nothing_quietly(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        structure = propose_structure(root)

        assert structure.documents == ()
        assert any("no readable text" in note for note in structure.notes)

    def test_a_missing_path_is_a_clean_error(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="no such file or folder"):
            propose_structure(tmp_path / "absent")

    def test_a_single_file_is_a_corpus_of_one(self, tmp_path: Path) -> None:
        # 4.9 offers a file, a folder or a tree as equals, and the commonest preface to
        # exclude arrives as one file. The file is its own root; its one document is named
        # for the file.
        path = tmp_path / "novel.md"
        path.write_text("It is a truth universally acknowledged.\n", encoding="utf-8", newline="")

        structure = propose_structure(path)

        assert [plan.path for plan in structure.documents] == ["novel.md"]
        assert structure.root == str(path.resolve())


class TestTheMapAsADocument:
    def test_every_proposal_carries_its_basis_into_the_document(self) -> None:
        payload = as_json(propose_structure(FIXTURES / "b"))
        entry = payload["documents"][0]

        for field in ("role", "addressing", "revision_of"):
            assert entry[field]["basis"], f"{field} has no basis"

    def test_it_is_json_serialisable(self) -> None:
        import json

        json.dumps(as_json(propose_structure(FIXTURES / "c")))

    def test_it_carries_the_regions(self) -> None:
        payload = as_json(propose_structure(FIXTURES / "b"))
        assert payload["documents"][0]["regions"][0]["label"] == "whole document"


class TestEverythingAUserReadsIsAscii:
    """The convention `IngestResult.summary` states: a Windows console under a legacy code
    page renders typographic punctuation as replacement characters, and output that looks
    corrupted is worse than output that looks plain."""

    def test_every_basis_and_note_survives_a_legacy_console(self) -> None:
        for fixture in ("b", "c"):
            structure = propose_structure(FIXTURES / fixture)
            for note in structure.notes:
                note.encode("ascii")
            for plan in structure.documents:
                for proposal in (plan.role, plan.addressing, plan.revision_of):
                    proposal.basis.encode("ascii")

    def test_a_rewrite_basis_is_ascii_too(self, tmp_path: Path) -> None:
        root = a_folder(
            tmp_path / "corpus",
            {"one/notes.md": "Ada met Bram.\n", "two/notes.md": "Something else.\n"},
        )
        plan = propose_structure(root).plan_for("two/notes.md")
        plan.revision_of.basis.encode("ascii")

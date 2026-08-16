"""Tests for the skeleton fixtures — reference corpora B, C, and D.

These fixtures carry no analysis, so there is no graph to check. What can be checked is that
they are internally consistent and that each still exhibits the property it exists to
exercise. A skeleton whose corpus.json has drifted from the files on disk is worse than no
fixture, because a later phase will develop against a description of a corpus that is not
there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SKELETONS = ("b", "c", "d")


def _corpus(name: str) -> dict:
    return json.loads((FIXTURES / name / "corpus.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpora() -> dict[str, dict]:
    return {name: _corpus(name) for name in SKELETONS}


# --- shared invariants -----------------------------------------------------------------


@pytest.mark.parametrize("name", SKELETONS)
def test_every_declared_document_exists(name: str) -> None:
    corpus = _corpus(name)
    missing = [
        entry["path"]
        for entry in corpus["documents"]
        if not (FIXTURES / name / entry["path"]).is_file()
    ]
    assert not missing, f"corpus.json names files that are not on disk: {missing}"


@pytest.mark.parametrize("name", SKELETONS)
def test_every_document_on_disk_is_declared(name: str) -> None:
    """The reverse direction. An undeclared file is a silent gap in the description."""
    corpus = _corpus(name)
    declared = {entry["path"] for entry in corpus["documents"]}
    on_disk = {
        str(path.relative_to(FIXTURES / name)).replace("\\", "/")
        for path in (FIXTURES / name).rglob("*.md")
        if path.name != "README.md"
    }
    assert on_disk == declared


@pytest.mark.parametrize("name", SKELETONS)
def test_document_roles_are_from_the_schema_vocabulary(name: str) -> None:
    corpus = _corpus(name)
    for entry in corpus["documents"]:
        assert entry["role"] in {"narrative", "reference"}


@pytest.mark.parametrize("name", SKELETONS)
def test_skeleton_carries_no_analysis(name: str) -> None:
    """Structure only. A graph here would be treated as ground truth by a later phase."""
    corpus = _corpus(name)
    for forbidden in ("characters", "relations", "snapshot", "analysis_runs"):
        assert forbidden not in corpus, f"{name} carries analysis it should not: {forbidden}"


@pytest.mark.parametrize("name", SKELETONS)
def test_skeleton_declares_its_segment_types(name: str) -> None:
    corpus = _corpus(name)
    assert corpus["segment_types"], "a work must declare how it is addressed"
    assert all(isinstance(segment, str) for segment in corpus["segment_types"])


@pytest.mark.parametrize("name", SKELETONS)
def test_skeleton_is_synthetic(name: str) -> None:
    """Nobody's unpublished work belongs in this repository."""
    assert "synthetic" in _corpus(name)["creator"]


@pytest.mark.parametrize("name", SKELETONS)
def test_skeleton_has_a_readme(name: str) -> None:
    readme = FIXTURES / name / "README.md"
    assert readme.is_file()
    assert len(readme.read_text(encoding="utf-8")) > 400


# --- B: revisions ----------------------------------------------------------------------


def test_b_has_more_than_one_revision(corpora: dict[str, dict]) -> None:
    assert len(corpora["b"]["revisions"]) >= 2


def test_b_revisions_differ_only_where_declared(corpora: dict[str, dict]) -> None:
    """The whole value of fixture B is that the diff has exactly one true positive."""
    expected = corpora["b"]["expected_diff"]
    base = FIXTURES / "b"

    for filename in expected["unchanged_documents"]:
        if filename == "cast.md":
            continue
        first = (base / "draft-1" / filename).read_text(encoding="utf-8")
        second = (base / "draft-2" / filename).read_text(encoding="utf-8")
        assert first == second, f"{filename} is declared unchanged but differs"

    for filename in expected["changed_documents"]:
        first = (base / "draft-1" / filename).read_text(encoding="utf-8")
        second = (base / "draft-2" / filename).read_text(encoding="utf-8")
        assert first != second, f"{filename} is declared changed but is identical"


def test_b_diff_is_attributed_to_the_text_not_the_analysis(corpora: dict[str, dict]) -> None:
    """Invariant 4 stated at the fixture level."""
    assert corpora["b"]["expected_diff"]["attribution"] == "text_revision"


def test_b_names_a_minor_character_control(corpora: dict[str, dict]) -> None:
    control = corpora["b"]["minor_character_control"]
    assert control["character"] in corpora["b"]["characters_present"]
    assert len(control["appears_in"]) == 1


# --- C: asserted versus observed --------------------------------------------------------


def test_c_has_both_narrative_and_reference_documents(corpora: dict[str, dict]) -> None:
    roles = {entry["role"] for entry in corpora["c"]["documents"]}
    assert roles == {"narrative", "reference"}


def test_c_carries_a_declared_but_unenacted_relation(corpora: dict[str, dict]) -> None:
    disagreements = corpora["c"]["provenance_disagreements"]
    assert disagreements["declared_but_never_enacted"]
    for entry in disagreements["declared_but_never_enacted"]:
        assert entry["expected_provenance"] == ["asserted"]


def test_c_carries_an_enacted_but_undeclared_relation(corpora: dict[str, dict]) -> None:
    disagreements = corpora["c"]["provenance_disagreements"]
    assert disagreements["enacted_but_never_declared"]
    for entry in disagreements["enacted_but_never_declared"]:
        assert entry["expected_provenance"] == ["observed"]


def test_c_disagreements_name_characters_the_corpus_lists(corpora: dict[str, dict]) -> None:
    known = {entry["name"] for entry in corpora["c"]["characters_present"]}
    disagreements = corpora["c"]["provenance_disagreements"]
    for group in ("declared_but_never_enacted", "enacted_but_never_declared"):
        for entry in disagreements[group]:
            assert set(entry["between"]) <= known


def test_c_character_flags_match_the_disagreements(corpora: dict[str, dict]) -> None:
    """A character claimed as declared-not-enacted must be flagged that way too."""
    by_name = {entry["name"]: entry for entry in corpora["c"]["characters_present"]}
    assert by_name["Tomas Reiner"]["declared"] is True
    assert by_name["Tomas Reiner"]["enacted"] is False
    assert by_name["Sister Yeong"]["declared"] is False
    assert by_name["Sister Yeong"]["enacted"] is True


def test_c_has_a_collective_character(corpora: dict[str, dict]) -> None:
    kinds = {entry["name"]: entry["kind"] for entry in corpora["c"]["characters_present"]}
    assert kinds["The Quorum"] == "collective"


def test_c_mixes_stages(corpora: dict[str, dict]) -> None:
    stages = {
        entry.get("stage") for entry in corpora["c"]["documents"] if entry["role"] == "narrative"
    }
    assert len(stages) > 1, "a real serial corpus is not uniformly drafted"


def test_c_conventions_are_declared_as_data_not_assumed(corpora: dict[str, dict]) -> None:
    conventions = corpora["c"]["conventions"]
    assert conventions["narrative_unit"] not in {"chapter", "episode", "scene"}
    assert "revision" in conventions["revision_convention"]


# --- D: editions ------------------------------------------------------------------------


def test_d_has_more_than_one_edition(corpora: dict[str, dict]) -> None:
    assert len(corpora["d"]["editions"]) >= 2


def test_d_editions_are_all_authoritative(corpora: dict[str, dict]) -> None:
    """Editions do not supersede one another; that is what separates D from B."""
    assert all(edition["authoritative"] for edition in corpora["d"]["editions"])


def test_d_cross_edition_identity_pairs_are_real_characters(corpora: dict[str, dict]) -> None:
    names = {entry["name"] for entry in corpora["d"]["characters_present"]}
    for left, right in corpora["d"]["cross_edition_identity"]["pairs"]:
        assert {left, right} <= names


def test_d_renamed_character_appears_in_exactly_one_edition_each(
    corpora: dict[str, dict],
) -> None:
    by_name = {entry["name"]: entry for entry in corpora["d"]["characters_present"]}
    for left, right in corpora["d"]["cross_edition_identity"]["pairs"]:
        assert by_name[left]["same_character_as"] == right
        assert by_name[right]["same_character_as"] == left
        assert not set(by_name[left]["in_editions"]) & set(by_name[right]["in_editions"])


def test_d_rename_is_actually_present_in_the_texts(corpora: dict[str, dict]) -> None:
    base = FIXTURES / "d"
    first = (base / "editions" / "1889-first" / "text.md").read_text(encoding="utf-8")
    revised = (base / "editions" / "1903-revised" / "text.md").read_text(encoding="utf-8")

    assert "Hesper" in first and "Perdita" not in first
    assert "Perdita" in revised and "Hesper" not in revised


def test_d_declares_a_mention_versus_presence_control(corpora: dict[str, dict]) -> None:
    control = corpora["d"]["mention_versus_presence"]
    assert control["mentioned_in"]
    assert control["appears_in"] == []

    for relative in control["mentioned_in"]:
        text = (FIXTURES / "d" / relative).read_text(encoding="utf-8")
        assert "magistrate" in text


def test_d_third_party_apparatus_is_separately_licensed(corpora: dict[str, dict]) -> None:
    apparatus = [entry for entry in corpora["d"]["documents"] if entry.get("third_party")]
    assert apparatus, "the scholarly shape needs third-party annotation"
    for entry in apparatus:
        assert entry["licence"]
        assert entry["role"] == "reference"


def test_d_expected_relations_name_an_edition(corpora: dict[str, dict]) -> None:
    known = {edition["id"] for edition in corpora["d"]["editions"]}
    for relation in corpora["d"]["expected_relations"]:
        assert relation["editions"]
        assert set(relation["editions"]) <= known

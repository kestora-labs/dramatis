"""Tests for reference corpus A.

The fixture's whole claim is that it was verified rather than invented. These tests are
what makes that claim checkable: the source text is the one whose hash is recorded, every
quotation is genuinely in it, the expectation floor is internally consistent with the
hand-authored graph, and each deliberately broken document fails for the stated reason.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dramatis.text import contains_quotation, normalise_whitespace
from dramatis.validation import IssueKind, validate_file

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "a"
SOURCE = FIXTURE / "source" / "pride-and-prejudice.txt"
SNAPSHOT = FIXTURE / "snapshot.json"
EXPECTATIONS = FIXTURE / "expectations.json"
INVALID = FIXTURE / "invalid"

RECORDED_SHA256 = "e3bb81d19b34dd917187e2836340b02dceb3dc751e18308092a0074bbb2118ab"


@pytest.fixture(scope="module")
def source_text() -> str:
    return SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expectations() -> dict:
    return json.loads(EXPECTATIONS.read_text(encoding="utf-8"))


def _evidence(snapshot: dict) -> list[tuple[str, dict]]:
    found = []
    for collection in ("characters", "relations"):
        for entry in snapshot[collection]:
            for piece in entry.get("evidence", []):
                found.append((entry["id"], piece))
    return found


# --- the source text -------------------------------------------------------------------


def test_source_matches_its_recorded_hash(source_text: str) -> None:
    """An accidental edit would silently change what the floor is checked against."""
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    assert digest == RECORDED_SHA256


def test_source_hash_is_recorded_consistently(snapshot: dict, expectations: dict) -> None:
    assert snapshot["documents"][0]["sha256"] == RECORDED_SHA256
    assert snapshot["text_revisions"][0]["sha256"] == RECORDED_SHA256
    assert expectations["source_sha256"] == RECORDED_SHA256
    assert RECORDED_SHA256 in (FIXTURE / "README.md").read_text(encoding="utf-8")


def test_source_carries_no_distributor_boilerplate(source_text: str) -> None:
    """Only the work is redistributed, not the transcriber's licence text or trademark."""
    head, tail = source_text[:4000], source_text[-4000:]
    for banned in ("START OF THE PROJECT GUTENBERG", "END OF THE PROJECT GUTENBERG"):
        assert banned not in source_text
    assert "Project Gutenberg License" not in head + tail


def test_source_begins_with_the_work(source_text: str) -> None:
    assert "It is a truth universally acknowledged" in source_text[:60000]


# --- the hand-authored snapshot --------------------------------------------------------


def test_snapshot_validates(snapshot: dict) -> None:
    assert validate_file(SNAPSHOT) == []


def test_every_quotation_is_verbatim_in_the_source(snapshot: dict, source_text: str) -> None:
    """Invariant 3, applied to the fixture itself."""
    missing = [
        (owner, piece["selector"]["exact"][:60])
        for owner, piece in _evidence(snapshot)
        if not contains_quotation(source_text, piece["selector"]["exact"])
    ]
    assert not missing, f"quotations not found in the source: {missing}"


def test_every_quotation_context_is_verbatim_too(snapshot: dict, source_text: str) -> None:
    """Prefix and suffix are what make re-anchoring work; a wrong one is worse than none."""
    normalised = normalise_whitespace(source_text)
    problems = []
    for owner, piece in _evidence(snapshot):
        selector = piece["selector"]
        for side in ("prefix", "suffix"):
            context = selector.get(side)
            if context and normalise_whitespace(context) not in normalised:
                problems.append((owner, side, context[:40]))
    assert not problems, f"context not found in the source: {problems}"


def _adjoins(haystack: str, left: str, right: str) -> bool:
    """Whether ``left`` is immediately followed by ``right`` in ``haystack``.

    Normalisation strips the ends of both, which loses whether a space separated them in
    the source. A join may therefore be tight — context ending mid-token, as an opening
    quotation mark does before the words it opens — or spaced. Both count.
    """
    return f"{left}{right}" in haystack or f"{left} {right}" in haystack


def test_prefix_and_suffix_actually_surround_the_quotation(
    snapshot: dict, source_text: str
) -> None:
    """Prefix and suffix are what make re-anchoring work; ones that don't adjoin are worse
    than none, because they would re-anchor to the wrong passage after an edit."""
    normalised = normalise_whitespace(source_text)
    problems = []
    for owner, piece in _evidence(snapshot):
        selector = piece["selector"]
        exact = normalise_whitespace(selector["exact"])
        prefix = normalise_whitespace(selector.get("prefix", ""))
        suffix = normalise_whitespace(selector.get("suffix", ""))
        if prefix and not _adjoins(normalised, prefix, exact):
            problems.append((owner, "prefix", prefix[-30:]))
        if suffix and not _adjoins(normalised, exact, suffix):
            problems.append((owner, "suffix", suffix[:30]))
    assert not problems, f"context does not adjoin the quotation: {problems}"


def test_every_evidence_locator_names_a_plausible_position(snapshot: dict) -> None:
    declared = snapshot["works"][0]["segment_types"]
    for owner, piece in _evidence(snapshot):
        path = piece["locator"]["path"]
        assert path, f"{owner} has an empty locator path"
        for segment in path:
            assert segment["type"] in declared, (
                f"{owner} uses segment type {segment['type']!r} which the work does not declare"
            )
            assert 1 <= segment["index"] <= 61


def test_relation_weights_share_one_basis(snapshot: dict) -> None:
    """Weights are comparable only within a basis; a fixture mixing them would be nonsense."""
    bases = {relation["weight_basis"] for relation in snapshot["relations"]}
    assert len(bases) == 1


def test_weights_are_declared_as_hand_assigned(snapshot: dict) -> None:
    """The fixture is authored, not counted, and must not pretend otherwise."""
    assert snapshot["relations"][0]["weight_basis"] == "hand_assigned_prominence"
    assert snapshot["analysis_runs"][0]["model"].startswith("none/")


# --- the expectation floor -------------------------------------------------------------


def test_floor_characters_all_appear_in_the_snapshot(snapshot: dict, expectations: dict) -> None:
    names = {character["name"] for character in snapshot["characters"]}
    missing = set(expectations["characters_that_must_be_present"]) - names
    assert not missing


def test_floor_alias_claims_match_the_snapshot(snapshot: dict, expectations: dict) -> None:
    by_name = {character["name"]: character for character in snapshot["characters"]}
    for claim in expectations["aliases_that_must_resolve"]:
        character = by_name[claim["to"]]
        known = set(character["aliases"]) | {character["name"]}
        missing = set(claim["aliases"]) - known
        assert not missing, f"{claim['to']} is missing aliases {missing}"


def test_floor_required_relations_all_appear_in_the_snapshot(
    snapshot: dict, expectations: dict
) -> None:
    by_id = {character["id"]: character["name"] for character in snapshot["characters"]}
    pairs = {
        frozenset((by_id[relation["source"]], by_id[relation["target"]]))
        for relation in snapshot["relations"]
    }
    for required in expectations["relations_that_must_exist"]:
        assert frozenset(required["between"]) in pairs, f"missing {required['between']}"


def test_floor_forbidden_relations_are_absent_from_the_snapshot(
    snapshot: dict, expectations: dict
) -> None:
    by_id = {character["id"]: character["name"] for character in snapshot["characters"]}
    pairs = {
        frozenset((by_id[relation["source"]], by_id[relation["target"]]))
        for relation in snapshot["relations"]
    }
    for forbidden in expectations["relations_that_must_not_exist"]:
        assert frozenset(forbidden["between"]) not in pairs


def test_floor_forbidden_pairs_name_characters_the_floor_requires(expectations: dict) -> None:
    """A negative control naming a character nobody extracts would never fire."""
    required = set(expectations["characters_that_must_be_present"])
    for forbidden in expectations["relations_that_must_not_exist"]:
        assert set(forbidden["between"]) <= required


def test_heaviest_relation_is_one_the_floor_allows(snapshot: dict, expectations: dict) -> None:
    by_id = {character["id"]: character["name"] for character in snapshot["characters"]}
    heaviest = max(snapshot["relations"], key=lambda relation: relation["weight"])
    pair = frozenset((by_id[heaviest["source"]], by_id[heaviest["target"]]))
    allowed = {
        frozenset(candidate) for candidate in expectations["heaviest_relation_must_be_among"]
    }
    assert pair in allowed


# --- the negative case -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected_kind"),
    [
        ("malformed-json.json", IssueKind.PARSE),
        ("missing-analysis-run-id.json", IssueKind.SCHEMA),
        ("dangling-relation-endpoint.json", IssueKind.REFERENCE),
        ("duplicate-character-id.json", IssueKind.DUPLICATE),
        ("weight-without-basis.json", IssueKind.SCHEMA),
        ("self-relation.json", IssueKind.REFERENCE),
    ],
)
def test_invalid_fixture_fails_for_the_stated_reason(
    filename: str, expected_kind: IssueKind
) -> None:
    issues = validate_file(INVALID / filename)

    assert issues, f"{filename} was expected to fail validation"
    assert expected_kind in {issue.kind for issue in issues}, (
        f"{filename} failed, but not as {expected_kind.value}: "
        f"{[(i.kind.value, i.message) for i in issues]}"
    )


def test_every_invalid_fixture_is_covered_by_a_test() -> None:
    """A file added to invalid/ without a test would silently prove nothing."""
    on_disk = {path.name for path in INVALID.glob("*.json")}
    covered = {
        "malformed-json.json",
        "missing-analysis-run-id.json",
        "dangling-relation-endpoint.json",
        "duplicate-character-id.json",
        "weight-without-basis.json",
        "self-relation.json",
    }
    assert on_disk == covered

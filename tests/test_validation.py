"""Tests for document validation.

The interesting cases are the ones JSON Schema cannot reach: an edge pointing at a
character that was never emitted, a snapshot naming a text revision that does not exist,
an identifier used twice. Each of those produces a structurally valid file that renders
as a wrong graph, which is the failure mode hardest to diagnose by eye.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from dramatis.validation import IssueKind, validate_document, validate_file
from tests.documents import minimal_document


def test_a_minimal_document_is_valid() -> None:
    assert validate_document(minimal_document()) == []


def test_missing_required_property_is_a_schema_issue() -> None:
    document = minimal_document()
    del document["snapshot"]["analysis_run_id"]

    issues = validate_document(document)

    assert [issue.kind for issue in issues] == [IssueKind.SCHEMA]
    assert "analysis_run_id" in issues[0].message


def test_unknown_property_is_rejected_rather_than_ignored() -> None:
    document = minimal_document()
    document["characters"][0]["nickname"] = "Addy"

    issues = validate_document(document)

    assert issues
    assert all(issue.kind is IssueKind.SCHEMA for issue in issues)


def test_relation_weight_without_a_basis_is_rejected() -> None:
    document = minimal_document()
    del document["relations"][0]["weight_basis"]

    issues = validate_document(document)

    assert any("weight_basis" in issue.message for issue in issues)


def test_dangling_relation_endpoint_is_a_reference_issue() -> None:
    document = minimal_document()
    document["relations"][0]["target"] = "char:missing"

    issues = validate_document(document)

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.REFERENCE
    assert issues[0].path == "/relations/0/target"
    assert "char:missing" in issues[0].message
    assert "known:" in issues[0].message, "the message should say what would have worked"


def test_snapshot_naming_an_absent_text_revision_is_caught() -> None:
    document = minimal_document()
    document["snapshot"]["text_revision_id"] = "rev:nope"

    issues = validate_document(document)

    assert [issue.path for issue in issues] == ["/snapshot/text_revision_id"]
    assert issues[0].kind is IssueKind.REFERENCE


def test_snapshot_naming_an_absent_analysis_run_is_caught() -> None:
    document = minimal_document()
    document["snapshot"]["analysis_run_id"] = "run:nope"

    issues = validate_document(document)

    assert [issue.path for issue in issues] == ["/snapshot/analysis_run_id"]


def test_duplicate_identifiers_are_reported_once_per_collection() -> None:
    document = minimal_document()
    document["characters"].append(deepcopy(document["characters"][0]))

    issues = validate_document(document)

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.DUPLICATE
    assert "char:a" in issues[0].message


def test_a_relation_may_not_join_a_character_to_itself() -> None:
    document = minimal_document()
    document["relations"][0]["target"] = "char:a"

    issues = validate_document(document)

    assert any("itself" in issue.message for issue in issues)


def test_evidence_pointing_at_an_absent_document_is_caught() -> None:
    document = minimal_document()
    document["relations"][0]["evidence"][0]["locator"]["document_id"] = "doc:nope"

    issues = validate_document(document)

    assert issues[0].path == "/relations/0/evidence/0/locator/document_id"


def test_schema_failures_suppress_reference_noise() -> None:
    """A malformed document should report its shape error, not a cascade of consequences."""
    document = minimal_document()
    del document["characters"]
    document["relations"][0]["target"] = "char:missing"

    issues = validate_document(document)

    assert all(issue.kind is IssueKind.SCHEMA for issue in issues)


def test_locator_segment_types_are_free_data() -> None:
    """Invariant 1 at the document level: an unfamiliar segment type must validate."""
    document = minimal_document()
    document["works"][0]["segment_types"] = ["movement", "plate", "utterance"]
    document["relations"][0]["evidence"][0]["locator"]["path"] = [
        {"type": "movement", "index": 2, "label": "The Turn"},
        {"type": "utterance", "index": 41},
    ]

    assert validate_document(document) == []


def test_validate_file_reads_and_checks(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(minimal_document()), encoding="utf-8")

    assert validate_file(path) == []


def test_invalid_json_reports_a_line_and_column(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version": "0.1.0",}', encoding="utf-8")

    issues = validate_file(path)

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.PARSE
    assert "line 1" in issues[0].path


def test_missing_file_is_reported_without_raising(tmp_path: Path) -> None:
    issues = validate_file(tmp_path / "absent.json")

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.PARSE


@pytest.mark.parametrize("document", [[], "text", 7, None])
def test_a_non_object_document_fails_cleanly(document: Any) -> None:
    issues = validate_document(document)

    assert issues
    assert all(issue.kind is IssueKind.SCHEMA for issue in issues)

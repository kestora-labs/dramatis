"""Validation of Dramatis snapshot documents.

Validation runs in two layers. The first is the published JSON Schema, which checks shape
and types. The second is referential integrity, which JSON Schema cannot express: that a
relation's endpoints name characters that exist, that a snapshot's two axes point at a real
text revision and a real analysis run, and that no identifier is used twice.

The second layer matters more in practice. A document that is structurally perfect but
whose edges point at characters that were never emitted will render as an empty graph, and
the cause will not be obvious from the file.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from dramatis.schema import load_schema


class IssueKind(StrEnum):
    SCHEMA = "schema"
    REFERENCE = "reference"
    DUPLICATE = "duplicate"
    PARSE = "parse"


@dataclass(frozen=True)
class Issue:
    """A single validation failure, located as precisely as the layer allows."""

    kind: IssueKind
    path: str
    message: str

    def __str__(self) -> str:
        location = self.path or "<document>"
        return f"{location}: {self.message}"


def _pointer(parts: Any) -> str:
    """Render a jsonschema error path as a JSON Pointer."""
    return "/" + "/".join(str(part) for part in parts) if parts else ""


def _schema_issues(document: Any) -> list[Issue]:
    validator = Draft202012Validator(load_schema())
    return [
        Issue(IssueKind.SCHEMA, _pointer(error.absolute_path), error.message)
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]


def _ids(document: dict, collection: str) -> list[str]:
    entries = document.get(collection) or []
    if not isinstance(entries, list):
        return []
    return [entry["id"] for entry in entries if isinstance(entry, dict) and "id" in entry]


def _duplicate_issues(document: dict) -> list[Issue]:
    issues: list[Issue] = []
    for collection in (
        "works",
        "documents",
        "text_revisions",
        "analysis_runs",
        "characters",
        "relations",
    ):
        counts = Counter(_ids(document, collection))
        for identifier, count in sorted(counts.items()):
            if count > 1:
                issues.append(
                    Issue(
                        IssueKind.DUPLICATE,
                        f"/{collection}",
                        f"identifier {identifier!r} is used {count} times; "
                        "identifiers must be unique within their kind",
                    )
                )
    return issues


def _reference_issues(document: dict) -> list[Issue]:
    issues: list[Issue] = []

    work_ids = set(_ids(document, "works"))
    document_ids = set(_ids(document, "documents"))
    revision_ids = set(_ids(document, "text_revisions"))
    run_ids = set(_ids(document, "analysis_runs"))
    character_ids = set(_ids(document, "characters"))

    def check(path: str, value: Any, universe: set[str], label: str) -> None:
        if isinstance(value, str) and value not in universe:
            known = ", ".join(sorted(universe)[:5]) or "none"
            issues.append(
                Issue(IssueKind.REFERENCE, path, f"unknown {label} {value!r} (known: {known})")
            )

    snapshot = document.get("snapshot")
    if isinstance(snapshot, dict):
        check("/snapshot/work_id", snapshot.get("work_id"), work_ids, "work")
        check(
            "/snapshot/text_revision_id",
            snapshot.get("text_revision_id"),
            revision_ids,
            "text revision",
        )
        check(
            "/snapshot/analysis_run_id",
            snapshot.get("analysis_run_id"),
            run_ids,
            "analysis run",
        )

    for index, entry in enumerate(document.get("documents") or []):
        if isinstance(entry, dict):
            check(f"/documents/{index}/work_id", entry.get("work_id"), work_ids, "work")

    for index, entry in enumerate(document.get("text_revisions") or []):
        if not isinstance(entry, dict):
            continue
        check(f"/text_revisions/{index}/work_id", entry.get("work_id"), work_ids, "work")
        for position, referenced in enumerate(entry.get("document_ids") or []):
            check(
                f"/text_revisions/{index}/document_ids/{position}",
                referenced,
                document_ids,
                "document",
            )

    for index, entry in enumerate(document.get("relations") or []):
        if not isinstance(entry, dict):
            continue
        check(f"/relations/{index}/source", entry.get("source"), character_ids, "character")
        check(f"/relations/{index}/target", entry.get("target"), character_ids, "character")
        if entry.get("source") is not None and entry.get("source") == entry.get("target"):
            issues.append(
                Issue(
                    IssueKind.REFERENCE,
                    f"/relations/{index}",
                    "a relation may not join a character to itself",
                )
            )

    for collection in ("characters", "relations"):
        for index, entry in enumerate(document.get(collection) or []):
            if not isinstance(entry, dict):
                continue
            for position, piece in enumerate(entry.get("evidence") or []):
                if not isinstance(piece, dict):
                    continue
                locator = piece.get("locator")
                if isinstance(locator, dict) and document_ids:
                    check(
                        f"/{collection}/{index}/evidence/{position}/locator/document_id",
                        locator.get("document_id"),
                        document_ids,
                        "document",
                    )

    return issues


def validate_document(document: Any) -> list[Issue]:
    """Return every problem with a parsed document. An empty list means it is valid."""
    issues = _schema_issues(document)
    if not isinstance(document, dict):
        return issues
    # Referential checks assume the shape held; running them on a malformed document
    # produces noise that buries the real error.
    if not issues:
        issues.extend(_duplicate_issues(document))
        issues.extend(_reference_issues(document))
    return issues


def validate_file(path: Path) -> list[Issue]:
    """Read, parse, and validate a document on disk."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        return [Issue(IssueKind.PARSE, str(path), f"cannot read file: {error.strerror}")]

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        return [
            Issue(
                IssueKind.PARSE,
                f"line {error.lineno}, column {error.colno}",
                f"invalid JSON: {error.msg}",
            )
        ]

    return validate_document(document)

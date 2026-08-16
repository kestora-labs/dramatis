"""Rendering and storing a snapshot.

A snapshot binds one text revision to one analysis run and holds the graph that pairing
produced. Both bindings are required, and they are the reason a Dramatis result can be
cited: a reader can tell whether a graph changed because the work was rewritten or because
the analysis improved, which is Invariant 4.

What is stored is the **rendered document**, not a normalised copy of it. The artifact kept
on disk is exactly the artifact exported and cited, so the archived and published forms
cannot drift apart, and reading one back needs no model and no network (Invariant 6).

Nothing is stored until it validates. A snapshot that fails the published schema is a bug
in the pipeline, and writing it would put an unreadable record under an identifier that
something may already cite.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from dramatis.aggregation import Aggregation
from dramatis.schema import DOCUMENT_VERSION
from dramatis.store import RegisteredCharacter, Store, StoredSnapshot, utc_now
from dramatis.validation import Issue, validate_document


class SnapshotError(Exception):
    """A snapshot could not be built or stored."""


def canonical_json(document: Any) -> str:
    """Serialise deterministically, so identical graphs hash identically."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def document_hash(document: Any) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisRun:
    """One execution of the pipeline, recorded in full so a result can be reproduced."""

    model: str
    prompt_version: str
    prompt_sha256: str | None = None
    provider: str | None = None
    pipeline_version: str | None = None
    application_version: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None

    @property
    def id(self) -> str:
        """Derived from everything that determines the analysis, including when it ran.

        Two executions of the same configuration are two runs, not one: models are not
        deterministic, and collapsing them would make a snapshot's identifier ambiguous
        between graphs that genuinely differ.
        """
        material = canonical_json(
            {
                "model": self.model,
                "provider": self.provider,
                "prompt_version": self.prompt_version,
                # The prompt itself, not the label somebody put on it: two runs under
                # differently-worded instructions are different configurations even when
                # both call themselves extract-v1.
                "prompt_sha256": self.prompt_sha256,
                "pipeline_version": self.pipeline_version,
                "parameters": self.parameters,
                "started_at": self.started_at,
            }
        )
        return f"run:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"

    def as_schema(self) -> dict[str, Any]:
        run: dict[str, Any] = {
            "id": self.id,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "parameters": dict(self.parameters),
        }
        for key in (
            "prompt_sha256",
            "provider",
            "pipeline_version",
            "application_version",
            "started_at",
            "completed_at",
        ):
            value = getattr(self, key)
            if value:
                run[key] = value
        return run


def snapshot_id(text_revision_id: str, analysis_run_id: str) -> str:
    material = f"{text_revision_id}\n{analysis_run_id}"
    return f"snap:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def _character_as_schema(character: RegisteredCharacter) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": character.id,
        "name": character.name,
        "kind": character.kind,
        "provenance": character.provenance,
        "review_status": character.review_status,
    }
    if character.aliases:
        entry["aliases"] = list(character.aliases)
    if character.notes:
        entry["notes"] = character.notes
    return entry


def build_document(
    store: Store,
    *,
    work_id: str,
    text_revision_id: str,
    run: AnalysisRun,
    character_ids: set[str],
    aggregation: Aggregation,
    label: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Render a schema-shaped document from what the store and the pipeline hold."""
    work = store.get_work(work_id)
    if work is None:
        raise SnapshotError(f"unknown work {work_id!r}")

    revision = store.get_text_revision(text_revision_id)
    if revision is None:
        raise SnapshotError(f"unknown text revision {text_revision_id!r}")

    collection = store.get_collection(work["collection_id"])
    if collection is None:
        raise SnapshotError(f"unknown collection {work['collection_id']!r}")

    documents = []
    for document_id in revision.document_ids:
        stored = store.get_document(document_id)
        if stored is None:
            raise SnapshotError(f"revision names a document that is gone: {document_id!r}")
        entry: dict[str, Any] = {
            "id": stored.id,
            "work_id": stored.work_id,
            "role": stored.role,
            "sha256": stored.sha256,
        }
        for key in ("title", "path", "media_type"):
            value = getattr(stored, key)
            if value:
                entry[key] = value
        documents.append(entry)

    work_entry: dict[str, Any] = {"id": work["id"], "title": work["title"]}
    for key in ("creator", "language", "edition"):
        if work.get(key):
            work_entry[key] = work[key]
    if work.get("segment_types"):
        work_entry["segment_types"] = work["segment_types"]

    revision_entry: dict[str, Any] = {
        "id": revision.id,
        "work_id": revision.work_id,
        "sha256": revision.sha256,
        "document_ids": list(revision.document_ids),
    }
    if revision.label:
        revision_entry["label"] = revision.label
    if revision.created_at:
        revision_entry["created_at"] = revision.created_at

    characters = [
        _character_as_schema(character)
        for character in store.list_characters(collection["id"])
        if character.id in character_ids
    ]

    snapshot: dict[str, Any] = {
        "id": snapshot_id(text_revision_id, run.id),
        "work_id": work_id,
        "text_revision_id": text_revision_id,
        "analysis_run_id": run.id,
        "created_at": created_at or utc_now(),
    }
    if label:
        snapshot["label"] = label

    collection_entry: dict[str, Any] = {"id": collection["id"], "name": collection["name"]}
    if collection.get("description"):
        collection_entry["description"] = collection["description"]

    return {
        "schema_version": DOCUMENT_VERSION,
        "collection": collection_entry,
        "works": [work_entry],
        "documents": documents,
        "text_revisions": [revision_entry],
        "analysis_runs": [run.as_schema()],
        "snapshot": snapshot,
        "characters": characters,
        "relations": [relation.as_schema() for relation in aggregation.relations],
    }


def save_snapshot(store: Store, document: dict[str, Any]) -> StoredSnapshot:
    """Validate a document and store it. Invalid documents are never written."""
    issues: list[Issue] = validate_document(document)
    if issues:
        detail = "; ".join(str(issue) for issue in issues[:5])
        raise SnapshotError(
            f"the snapshot does not satisfy the published schema and was not stored: {detail}"
        )

    run = document["analysis_runs"][0]
    store.upsert_analysis_run(run)

    snapshot = document["snapshot"]
    stored = StoredSnapshot(
        id=snapshot["id"],
        work_id=snapshot["work_id"],
        text_revision_id=snapshot["text_revision_id"],
        analysis_run_id=snapshot["analysis_run_id"],
        label=snapshot.get("label"),
        schema_version=document["schema_version"],
        sha256=document_hash(document),
        created_at=snapshot.get("created_at") or utc_now(),
        document=document,
    )
    store.insert_snapshot(stored)
    return stored

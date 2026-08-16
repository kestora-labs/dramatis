"""Sample documents shared between test modules.

Not a test module. Kept separate so tests never import one another.
"""

from __future__ import annotations

from typing import Any

SHA = "0" * 64


def minimal_document() -> dict[str, Any]:
    """A small snapshot document that is valid in every respect.

    Returns a fresh copy each call so tests may mutate it freely.
    """
    return {
        "schema_version": "0.1.0",
        "collection": {"id": "col:test", "name": "Test collection"},
        "works": [
            {
                "id": "work:1",
                "title": "A Work",
                "segment_types": ["part", "section", "paragraph"],
            }
        ],
        "documents": [{"id": "doc:1", "work_id": "work:1", "role": "narrative", "sha256": SHA}],
        "text_revisions": [
            {"id": "rev:1", "work_id": "work:1", "sha256": SHA, "document_ids": ["doc:1"]}
        ],
        "analysis_runs": [
            {"id": "run:1", "model": "claude-opus-5", "prompt_version": "extract-v1"}
        ],
        "snapshot": {
            "id": "snap:1",
            "work_id": "work:1",
            "text_revision_id": "rev:1",
            "analysis_run_id": "run:1",
        },
        "characters": [
            {"id": "char:a", "name": "Ada", "aliases": ["Miss A"], "provenance": "observed"},
            {"id": "char:b", "name": "Bram", "provenance": "asserted"},
        ],
        "relations": [
            {
                "id": "rel:a-b",
                "source": "char:a",
                "target": "char:b",
                "weight": 12,
                "weight_basis": "shared_segments",
                "types": ["alliance"],
                "provenance": "observed",
                "evidence": [
                    {
                        "locator": {
                            "document_id": "doc:1",
                            "path": [{"type": "section", "index": 3}],
                        },
                        "selector": {"exact": "They met at the gate."},
                        "note": "First meeting.",
                    }
                ],
            }
        ],
    }

"""Tests for the Dramatis schema itself.

Two things are being defended here. First, that the schema is a well-formed JSON Schema
whose internal references all resolve — a broken $ref fails silently at validation time,
accepting documents it should reject. Second, and more importantly, Invariant 1: the
schema must name no unit belonging to any particular medium.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schema"
SCHEMA_PATH = SCHEMA_DIR / "dramatis.schema.json"

# Invariant 1. If one of these ever needs to appear in the schema, the design is wrong:
# structural position is an ordered path of typed segments, and the types are data.
MEDIUM_SPECIFIC_TERMS = ["chapter", "panel", "beat", "episode", "scene", "issue", "stanza"]


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_file_exists_and_is_json(schema: dict) -> None:
    assert schema["title"] == "Dramatis snapshot document"


def test_schema_is_a_valid_2020_12_schema(schema: dict) -> None:
    Draft202012Validator.check_schema(schema)


CANONICAL_HOST = "kestoralabs.co.uk"


def test_schema_declares_a_stable_id_and_dialect(schema: dict) -> None:
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].startswith("https://")
    assert "/0.1/" in schema["$id"], "the $id must pin the schema's own version"


def test_schema_id_uses_a_domain_the_project_owns(schema: dict) -> None:
    """The $id is a public identifier that outlives the file.

    Other tools identify the format by this string, and documents record it. Pointing it at
    a domain the project does not control means the identifier could one day be claimed by
    someone else, and cannot be resolved to a published copy by anyone.
    """
    host = urlparse(schema["$id"]).netloc
    assert host == CANONICAL_HOST, (
        f"$id host is {host!r}; the schema must be identified under {CANONICAL_HOST!r}, "
        "which the project actually owns"
    )


def test_documentation_quotes_the_actual_id(schema: dict) -> None:
    """The docs print the identifier verbatim; a version bump must update both together.

    Phase 6.5 serves the schema at this exact URL, so a documented URL that has drifted
    from the file would be published as the canonical reference and be wrong.
    """
    documented = (ROOT / "docs" / "schema.md").read_text(encoding="utf-8")
    assert schema["$id"] in documented, (
        f"docs/schema.md does not quote the schema's own $id ({schema['$id']})"
    )


def test_roadmap_schedules_publication_at_the_owned_host() -> None:
    roadmap = (ROOT / "AI" / "ROADMAP.md").read_text(encoding="utf-8")
    assert CANONICAL_HOST in roadmap, "6.5 must name where the schema gets served"


@pytest.mark.parametrize("term", MEDIUM_SPECIFIC_TERMS)
def test_schema_names_no_medium_specific_unit(term: str) -> None:
    """Invariant 1, enforced across every file in schema/."""
    pattern = re.compile(rf"\b{term}s?\b", re.IGNORECASE)
    offenders = [
        f"{path.name}:{n}"
        for path in sorted(SCHEMA_DIR.rglob("*.json"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"'{term}' appears in the schema at {offenders}. Structural position is an "
        "ordered path of typed segments; segment types are supplied per work as data."
    )


def _collect_refs(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        for value in node.values():
            found.extend(_collect_refs(value))
        return found
    if isinstance(node, list):
        return [ref for item in node for ref in _collect_refs(item)]
    return []


def test_every_internal_ref_resolves(schema: dict) -> None:
    defs = schema["$defs"]
    for ref in _collect_refs(schema):
        assert ref.startswith("#/$defs/"), f"unexpected non-local $ref: {ref}"
        name = ref.removeprefix("#/$defs/")
        assert name in defs, f"$ref points at a definition that does not exist: {ref}"


def test_no_definition_is_unreachable(schema: dict) -> None:
    referenced = {ref.removeprefix("#/$defs/") for ref in _collect_refs(schema)}
    orphans = set(schema["$defs"]) - referenced
    assert not orphans, f"unreachable definitions: {sorted(orphans)}"


def test_provenance_carries_the_three_classes(schema: dict) -> None:
    """Invariant 5: every node and edge records where its claim came from."""
    assert schema["$defs"]["provenance"]["enum"] == ["observed", "asserted", "human"]
    for kind in ("character", "relation"):
        assert "provenance" in schema["$defs"][kind]["required"]


def test_review_status_supports_corrections_surviving_reanalysis(schema: dict) -> None:
    assert schema["$defs"]["reviewStatus"]["enum"] == [
        "proposed",
        "accepted",
        "corrected",
        "rejected",
    ]


def test_snapshot_binds_both_time_axes_separately(schema: dict) -> None:
    """Invariant 4: text revision and analysis run must never be collapsed."""
    required = schema["$defs"]["snapshot"]["required"]
    assert "text_revision_id" in required
    assert "analysis_run_id" in required


def test_analysis_run_records_what_citation_requires(schema: dict) -> None:
    run = schema["$defs"]["analysisRun"]
    assert "model" in run["required"]
    assert "prompt_version" in run["required"]
    assert "application_version" in run["properties"]


def test_relation_weight_is_meaningless_without_its_basis(schema: dict) -> None:
    required = schema["$defs"]["relation"]["required"]
    assert "weight" in required
    assert "weight_basis" in required


def test_evidence_requires_a_reanchorable_quotation(schema: dict) -> None:
    """Invariant 3 depends on there always being an exact quotation to verify."""
    evidence = schema["$defs"]["evidence"]
    assert evidence["required"] == ["locator", "selector"]
    assert schema["$defs"]["selector"]["required"] == ["exact"]


def test_locator_is_an_ordered_path_of_typed_segments(schema: dict) -> None:
    locator = schema["$defs"]["locator"]
    assert locator["required"] == ["path"]
    assert locator["properties"]["path"]["minItems"] == 1

    segment_type = schema["$defs"]["segment"]["properties"]["type"]
    assert segment_type["type"] == "string", "segment type must be free data, not an enum"
    assert "enum" not in segment_type


def test_relation_types_are_not_a_closed_vocabulary(schema: dict) -> None:
    types = schema["$defs"]["relation"]["properties"]["types"]
    assert "enum" not in types["items"]

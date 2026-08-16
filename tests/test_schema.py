"""Tests for the Dramatis schema itself.

Three things are being defended here. First, that the schema is a well-formed JSON Schema
whose internal references all resolve — a broken $ref fails silently at validation time,
accepting documents it should reject. Second, and most importantly, Invariant 1: the
schema must name no unit belonging to any particular medium. Third, that the document is
where an *installed* copy can reach it (D20), which is why every test here addresses it as
a package resource rather than by a path within the checkout.
"""

import json
import re
from importlib.resources import as_file, files
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jsonschema import Draft202012Validator

import dramatis
from dramatis.schema import SCHEMA_FILENAME, SCHEMA_RESOURCE, load_schema

ROOT = Path(__file__).resolve().parents[1]

# Invariant 1. If one of these ever needs to appear in the schema, the design is wrong:
# structural position is an ordered path of typed segments, and the types are data.
MEDIUM_SPECIFIC_TERMS = ["chapter", "panel", "beat", "episode", "scene", "issue", "stanza"]


@pytest.fixture(scope="module")
def schema() -> dict:
    return load_schema()


def test_the_schema_ships_with_the_package() -> None:
    """D20. The regression test for a fault a checkout cannot see.

    `[tool.hatch.build.targets.wheel] packages = ["src/dramatis"]` ships the package
    directory and nothing else. While the schema sat at the repository root and was reached
    by walking up from `__file__`, every test passed here and `dramatis validate` raised
    FileNotFoundError for everyone who had installed rather than cloned.

    So this asks the question the way an installed copy asks it — for a resource of the
    `dramatis.schema` package — and not by any path that exists only in a checkout. A test
    reading `<repo>/schema/dramatis.schema.json` would have passed throughout the fault.
    """
    resource = files("dramatis.schema") / SCHEMA_FILENAME
    assert resource.is_file(), (
        f"{SCHEMA_FILENAME} is not a resource of the dramatis.schema package. It must ship "
        "inside src/dramatis/, since that is all the wheel contains."
    )
    assert json.loads(resource.read_text(encoding="utf-8"))["title"]


def test_the_loader_does_not_reach_outside_the_package() -> None:
    """The file shipping is not enough: the loader must be the thing that reads it.

    A copy inside the package while `load_schema()` still walked up from `__file__` would
    satisfy the test above and fail exactly as before.
    """
    package_root = Path(dramatis.__file__).resolve().parent
    with as_file(SCHEMA_RESOURCE) as path:
        resolved = path.resolve()
    assert package_root in resolved.parents, (
        f"the schema is read from {resolved}, which is outside the installed package at "
        f"{package_root}. Anything reached by leaving the package resolves in a checkout "
        "and nowhere else."
    )


@pytest.mark.parametrize("document", ["docs/schema.md", "NOTICE"])
def test_the_documented_location_is_where_the_schema_actually_is(document: str) -> None:
    """Both point a reader at the file: the reference docs, and the licence notice.

    NOTICE is the one that matters legally — it is what says this document is CC BY 4.0
    rather than Apache-2.0, and it can only say so by naming it.
    """
    shipped = "src/dramatis/schema/dramatis.schema.json"
    text = (ROOT / document).read_text(encoding="utf-8")
    assert shipped in text, f"{document} does not name the schema's actual location ({shipped})"


def test_no_copy_of_the_schema_is_left_at_the_repository_root() -> None:
    """One document, one home. Two would drift, and the served copy is the public one."""
    assert not (ROOT / "schema").exists(), (
        "schema/ has reappeared at the repository root. The schema is published from "
        "src/dramatis/schema/, and a second copy is a second thing to keep in step."
    )


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
    """Invariant 1, enforced across every schema document the package ships."""
    pattern = re.compile(rf"\b{term}s?\b", re.IGNORECASE)
    documents = sorted(
        (entry for entry in files("dramatis.schema").iterdir() if entry.name.endswith(".json")),
        key=lambda entry: entry.name,
    )
    assert documents, "no schema document found in the package"
    offenders = [
        f"{document.name}:{n}"
        for document in documents
        for n, line in enumerate(document.read_text(encoding="utf-8").splitlines(), start=1)
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

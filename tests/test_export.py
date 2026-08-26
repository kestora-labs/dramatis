"""Handing a reading to somebody else's tool (**6.1**).

The acceptance criterion for this phase is that *"a Dramatis graph opens correctly in Gephi
via GEXF"*, and nothing in a test suite can open Gephi. What a suite can do is hold the
export to the structure Gephi reads — the namespace, the declared attributes, the native
label and weight — and to the promises the schema and the invariants make about what must
never be dropped on the way out.

Those promises are what most of this file is about. A weight without its basis, a claim
without its provenance, and an edge exported as `proposed` after somebody rejected it are
each a correct-looking file that says something untrue.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from dramatis.export import (
    CSV,
    EDGE_COLUMNS,
    FORMATS,
    GEXF,
    GEXF_NAMESPACE,
    GLOBAL_PREFIXES,
    GRAPHML,
    GRAPHML_NAMESPACE,
    JSONLD,
    NODE_COLUMNS,
    SCOPED_PREFIXES,
    ExportError,
    export_document,
)
from tests.documents import minimal_document

GRAPHML_NS = {"g": GRAPHML_NAMESPACE}
GEXF_NS = {"x": GEXF_NAMESPACE}


def a_document(**changes: Any) -> dict[str, Any]:
    document = minimal_document()
    document.update(changes)
    return document


def only(export) -> str:
    part = export.single
    assert part is not None
    return part.text


def graphml_of(document: dict[str, Any], **kwargs) -> ET.Element:
    return ET.fromstring(only(export_document(document, GRAPHML, **kwargs)))


def gexf_of(document: dict[str, Any], **kwargs) -> ET.Element:
    return ET.fromstring(only(export_document(document, GEXF, **kwargs)))


def jsonld_of(document: dict[str, Any], **kwargs) -> dict[str, Any]:
    return json.loads(only(export_document(document, JSONLD, **kwargs)))


def csv_of(document: dict[str, Any], **kwargs) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    export = export_document(document, CSV, **kwargs)
    nodes, edges = export.parts
    return (
        list(csv.DictReader(io.StringIO(nodes.text))),
        list(csv.DictReader(io.StringIO(edges.text))),
    )


# -- what every format must carry -----------------------------------------------------


class TestNothingLosesTheWeightBasis:
    """The schema requires it in as many words: *"must be carried through every export"*.

    A weight is a number on a named scale. Every one of these formats has a native place to
    put the number and none has a place to put the scale, which is exactly why it is the
    field that goes missing.
    """

    def test_graphml_declares_it_and_writes_it(self) -> None:
        root = graphml_of(a_document())

        declared = {key.get("attr.name") for key in root.findall("g:key[@for='edge']", GRAPHML_NS)}
        assert {"weight", "weight_basis"} <= declared

        edge = root.find("g:graph/g:edge", GRAPHML_NS)
        assert edge is not None
        written = {data.get("key"): data.text for data in edge.findall("g:data", GRAPHML_NS)}
        assert written["e_weight"] == "12"
        assert written["e_weight_basis"] == "shared_segments"

    def test_gexf_puts_the_weight_where_gephi_reads_it_and_the_basis_beside_it(self) -> None:
        root = gexf_of(a_document())

        edge = root.find("x:graph/x:edges/x:edge", GEXF_NS)
        assert edge is not None
        # Native, not an attvalue: Gephi reads an edge's thickness from this attribute and
        # would treat a custom one as an ordinary column.
        assert edge.get("weight") == "12"

        values = {
            entry.get("for"): entry.get("value")
            for entry in edge.findall("x:attvalues/x:attvalue", GEXF_NS)
        }
        assert values["weight_basis"] == "shared_segments"

    def test_csv_gives_the_basis_its_own_column(self) -> None:
        _, edges = csv_of(a_document())

        assert "weight_basis" in EDGE_COLUMNS
        assert edges[0]["weight"] == "12"
        assert edges[0]["weight_basis"] == "shared_segments"

    def test_jsonld_keeps_both(self) -> None:
        rendered = jsonld_of(a_document())

        assert rendered["relations"][0]["weight"] == 12
        assert rendered["relations"][0]["weight_basis"] == "shared_segments"


class TestProvenanceSurvivesEveryFormat:
    """Invariant 5. A graph that cannot tell an enacted relation from a declared one asserts
    things the narrative never shows."""

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_an_asserted_node_is_still_asserted_after_the_round_trip(self, fmt: str) -> None:
        export = export_document(a_document(), fmt)
        text = "\n".join(part.text for part in export.parts)

        assert "asserted" in text
        assert "observed" in text

    def test_csv_carries_it_per_row(self) -> None:
        nodes, edges = csv_of(a_document())

        assert [node["provenance"] for node in nodes] == ["observed", "asserted"]
        assert edges[0]["provenance"] == "observed"


class TestTheReadingIsIdentifiedInEveryFormat:
    """An export is the copy that gets cited, and Invariant 4 says a citation names both
    axes: which text, and which analysis."""

    def test_graphml_carries_the_whole_provenance_block(self) -> None:
        root = graphml_of(a_document())

        graph = root.find("g:graph", GRAPHML_NS)
        assert graph is not None
        written = {data.get("key"): data.text for data in graph.findall("g:data", GRAPHML_NS)}

        assert written["g_snapshot_id"] == "snap:1"
        assert written["g_text_revision_id"] == "rev:1"
        assert written["g_analysis_run_id"] == "run:1"
        assert written["g_model"] == "claude-opus-5"
        assert written["g_prompt_version"] == "extract-v1"

    def test_gexf_says_it_in_the_one_line_it_has_room_for(self) -> None:
        root = gexf_of(a_document())

        description = root.findtext("x:meta/x:description", namespaces=GEXF_NS) or ""
        for expected in ("A Work", "snap:1", "rev:1", "run:1", "claude-opus-5"):
            assert expected in description

    def test_every_csv_row_carries_the_snapshot_it_came_from(self) -> None:
        """A spreadsheet has no header above the header. Without this, two exports loaded
        into one sheet are indistinguishable."""
        nodes, edges = csv_of(a_document())

        assert NODE_COLUMNS[0] == "snapshot_id"
        assert {node["snapshot_id"] for node in nodes} == {"snap:1"}
        assert {edge["snapshot_id"] for edge in edges} == {"snap:1"}

    def test_jsonld_carries_both_axes_and_what_it_conforms_to(self) -> None:
        rendered = jsonld_of(a_document())

        assert rendered["id"] == "snap:1"
        assert rendered["text_revision"]["id"] == "rev:1"
        assert rendered["analysis_run"]["id"] == "run:1"
        assert rendered["conforms_to"].endswith("dramatis.schema.json")


# -- review is read over the document -------------------------------------------------


class TestStandingReviewDecisionsAreApplied:
    """**5.1** and **D50**: a decision lives beside the snapshot and supersedes what the
    snapshot declared. An export that read the document alone would publish a cast somebody
    has since rejected."""

    REVIEW = {("character", "char:a"): "rejected", ("relation", "rel:a-b"): "accepted"}

    def test_a_rejected_character_exports_as_rejected(self) -> None:
        nodes, _ = csv_of(a_document(), review=self.REVIEW)

        assert nodes[0]["id"] == "char:a"
        assert nodes[0]["review_status"] == "rejected"

    def test_an_accepted_edge_exports_as_accepted(self) -> None:
        """The sharper case: nothing in the pipeline writes a `review_status` onto an edge,
        so without the overlay every edge would export as proposed forever."""
        _, edges = csv_of(a_document(), review=self.REVIEW)

        assert edges[0]["review_status"] == "accepted"

    def test_a_subject_nobody_ruled_on_keeps_what_the_document_declared(self) -> None:
        document = a_document()
        document["characters"][1]["review_status"] = "accepted"

        rendered = jsonld_of(document, review={("character", "char:a"): "rejected"})

        assert rendered["characters"][0]["review_status"] == "rejected"
        assert rendered["characters"][1]["review_status"] == "accepted"

    def test_without_an_overlay_the_document_stands_alone(self) -> None:
        """Correct for a document that arrived from outside a store, where there is no
        decision to apply and inventing one would be worse than saying nothing."""
        rendered = jsonld_of(a_document())

        assert "review_status" not in rendered["relations"][0]


# -- evidence, and where it went ------------------------------------------------------


class TestEvidenceIsCountedRatherThanFlattened:
    """**6.2** exports evidence as W3C Web Annotation. Until then a claim says how much
    backs it, so nothing silently reads as unevidenced, but no format here mangles a
    locator and a selector into a string."""

    def test_a_claim_reports_how_much_evidence_backs_it(self) -> None:
        _, edges = csv_of(a_document())

        assert edges[0]["evidence_count"] == "1"

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_no_format_smuggles_the_quotation_out(self, fmt: str) -> None:
        export = export_document(a_document(), fmt)
        text = "\n".join(part.text for part in export.parts)

        assert "They met at the gate." not in text


# -- the shapes the readers expect ----------------------------------------------------


class TestGraphMLIsTheShapeAReaderExpects:
    def test_it_is_in_the_graphml_namespace(self) -> None:
        root = graphml_of(a_document())

        assert root.tag == f"{{{GRAPHML_NAMESPACE}}}graphml"

    def test_every_key_a_datum_names_is_declared(self) -> None:
        """An undeclared key is the fault that makes a file open and come up empty."""
        root = graphml_of(a_document())

        declared = {key.get("id") for key in root.findall("g:key", GRAPHML_NS)}
        used = {data.get("key") for data in root.iter(f"{{{GRAPHML_NAMESPACE}}}data")}

        assert used <= declared

    def test_graph_scoped_data_comes_before_the_nodes(self) -> None:
        """The DTD orders a graph's children, and a reader that validates will refuse a file
        that puts them the other way round."""
        root = graphml_of(a_document())
        graph = root.find("g:graph", GRAPHML_NS)
        assert graph is not None

        tags = [child.tag.rsplit("}", 1)[-1] for child in graph]
        assert tags.index("data") < tags.index("node")

    def test_direction_is_on_the_edge_rather_than_in_a_key(self) -> None:
        """GraphML has a native place for it. A second copy in a `<data>` is a second copy
        that can disagree with the first."""
        document = a_document()
        document["relations"][0]["directed"] = True

        root = graphml_of(document)
        edge = root.find("g:graph/g:edge", GRAPHML_NS)
        assert edge is not None
        assert edge.get("directed") == "true"

        declared = {key.get("attr.name") for key in root.findall("g:key[@for='edge']", GRAPHML_NS)}
        assert "directed" not in declared


class TestGEXFIsTheShapeGephiExpects:
    def test_it_declares_the_version_gephi_reads(self) -> None:
        root = gexf_of(a_document())

        assert root.tag == f"{{{GEXF_NAMESPACE}}}gexf"
        assert root.get("version") == "1.2"

    def test_a_node_is_captioned_with_the_character_rather_than_the_identifier(self) -> None:
        """The commonest way a correct export looks broken: every node in the layout reading
        `char:something`."""
        root = gexf_of(a_document())

        labels = [node.get("label") for node in root.findall("x:graph/x:nodes/x:node", GEXF_NS)]
        assert labels == ["Ada", "Bram"]

    def test_every_attvalue_names_a_declared_attribute(self) -> None:
        root = gexf_of(a_document())

        for scope, container in (("node", "nodes"), ("edge", "edges")):
            declared = {
                entry.get("id")
                for entry in root.findall(
                    f"x:graph/x:attributes[@class='{scope}']/x:attribute", GEXF_NS
                )
            }
            used = {
                entry.get("for")
                for entry in root.findall(
                    f"x:graph/x:{container}/x:{scope}/x:attvalues/x:attvalue", GEXF_NS
                )
            }
            assert used <= declared, f"{scope} uses attributes it never declared"

    def test_a_directed_relation_is_typed_directed(self) -> None:
        document = a_document()
        document["relations"][0]["directed"] = True

        root = gexf_of(document)
        edge = root.find("x:graph/x:edges/x:edge", GEXF_NS)
        assert edge is not None
        assert edge.get("type") == "directed"

    def test_an_undirected_graph_says_so_by_default(self) -> None:
        root = gexf_of(a_document())
        graph = root.find("x:graph", GEXF_NS)
        assert graph is not None

        assert graph.get("defaultedgetype") == "undirected"


class TestTheCSVPair:
    def test_the_two_parts_are_named_apart(self) -> None:
        export = export_document(a_document(), CSV)

        assert [part.suffix for part in export.parts] == [".nodes.csv", ".edges.csv"]
        assert export.single is None

    def test_headers_are_the_declared_columns_in_order(self) -> None:
        export = export_document(a_document(), CSV)
        nodes, edges = export.parts

        assert nodes.text.splitlines()[0] == ",".join(NODE_COLUMNS)
        assert edges.text.splitlines()[0] == ",".join(EDGE_COLUMNS)

    def test_a_field_the_document_omitted_is_blank_rather_than_invented(self) -> None:
        """**D54**'s distinction, in a spreadsheet: an absent confidence is not a low one."""
        nodes, _ = csv_of(a_document())

        assert nodes[0]["confidence"] == ""
        assert nodes[0]["salience"] == ""

    def test_a_multi_valued_field_stays_in_one_cell(self) -> None:
        document = a_document()
        document["characters"][0]["aliases"] = ["Miss A", "A, of the Grange"]

        nodes, _ = csv_of(document)

        assert nodes[0]["aliases"] == "Miss A; A, of the Grange"

    def test_line_endings_do_not_depend_on_the_machine(self) -> None:
        export = export_document(a_document(), CSV)

        assert "\r" not in export.parts[0].text


class TestJSONLD:
    def test_an_identifier_prefix_expands_to_somewhere(self) -> None:
        """Every Dramatis identifier is already namespaced by kind, which is the shape of a
        compact IRI. The context only has to say what each prefix expands to."""
        context = jsonld_of(a_document())["@context"]

        for prefix in ("col", "work", "char", "rel", "rev", "run", "snap", "doc"):
            assert isinstance(context[prefix], str), f"{prefix} is not usable as a prefix"
            assert context[prefix].startswith("https://")

    def test_a_name_derived_identifier_is_scoped_to_its_registry(self) -> None:
        """`char:mary` is one person in one collection and a different person in another.
        Two exports meeting in one triple store must not merge them."""
        context = jsonld_of(a_document())["@context"]

        assert context["char"].endswith("/collection/test/char/")
        assert context["rel"].startswith(context["char"].rsplit("/char/", 1)[0])

    def test_a_content_derived_identifier_is_not_scoped(self) -> None:
        """The opposite case, and the reason the two are split: the same text has the same
        revision identifier in this store and in any other, which is what makes two
        independently produced snapshots comparable."""
        context = jsonld_of(a_document())["@context"]

        assert "/collection/" not in context["rev"]
        assert "/collection/" not in context["run"]

    def test_two_collections_do_not_share_a_character_namespace(self) -> None:
        other = a_document()
        other["collection"] = {"id": "col:another", "name": "Another"}

        assert jsonld_of(a_document())["@context"]["char"] != jsonld_of(other)["@context"]["char"]

    def test_no_term_is_both_a_prefix_and_a_property(self) -> None:
        """`work:1` stops expanding the moment `work` is also defined as a property, and the
        identifier quietly becomes an IRI with a scheme of `work`. Checked over every key in
        the document, not only the top level: a collision anywhere is the same collision."""
        rendered = jsonld_of(a_document())

        prefixes = {"col", *GLOBAL_PREFIXES, *SCOPED_PREFIXES}

        def keys(value: Any) -> set[str]:
            if isinstance(value, dict):
                return set(value) | {name for entry in value.values() for name in keys(entry)}
            if isinstance(value, list):
                return {name for entry in value for name in keys(entry)}
            return set()

        assert not (prefixes & keys({k: v for k, v in rendered.items() if k != "@context"}))

    def test_free_form_objects_are_kept_as_json(self) -> None:
        """Whatever knobs somebody's provider took are not predicates in this vocabulary."""
        document = a_document()
        document["analysis_runs"][0]["parameters"] = {"temperature": 0}

        rendered = jsonld_of(document)

        assert rendered["analysis_run"]["parameters"] == {"temperature": 0}
        assert rendered["@context"]["parameters"]["@type"] == "@json"

    def test_the_schema_field_name_is_kept_rather_than_renamed(self) -> None:
        """Nothing in JSON-LD is drawing a node, so there is no reason to call a name a
        label here — unlike the three formats that are."""
        rendered = jsonld_of(a_document())

        assert rendered["characters"][0]["name"] == "Ada"
        assert "label" not in rendered["characters"][0]


# -- refusals -------------------------------------------------------------------------


class TestWhatItRefuses:
    def test_an_unknown_format_names_the_ones_there_are(self) -> None:
        with pytest.raises(ExportError) as raised:
            export_document(a_document(), "dot")

        assert "dot" in str(raised.value)
        assert "gexf" in str(raised.value)

    def test_something_that_is_not_a_snapshot_document_is_refused(self) -> None:
        with pytest.raises(ExportError) as raised:
            export_document({"schema_version": "0.1.0"}, GEXF)

        assert "snapshot" in str(raised.value)

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_a_graph_with_nobody_in_it_still_exports(self, fmt: str) -> None:
        """A reading that found no one is a finding, not a failure."""
        document = a_document(characters=[], relations=[])

        export = export_document(document, fmt)

        assert all(part.text for part in export.parts)

"""Getting a reading out of Dramatis and into somebody else's tool.

Two exports, because a reading is two things. **The graph** (**6.1**) goes out as GraphML or
GEXF for the network tools a digital humanist already has open, as CSV node and edge lists
for the spreadsheet and the R session, or as JSON-LD for the catalogue that wants to link it
to something else. **The evidence** (**6.2**) goes out as W3C Web Annotation, which is where
the quotations are.

Nothing here calls a model or reaches a network (Invariant 6). An export is arithmetic over
a document the store already holds, which is the point: a snapshot must be readable, and
now movable, by somebody with no key and no connection.

Four rules decide what an export contains.

**The weight basis travels with every weight, in every format.** The schema says so in as
many words — *"Weights are comparable only within a shared basis, so this is required and
must be carried through every export"* — and it is the one field these formats make it
easy to lose, because every one of them has a native notion of edge weight and none of them
has a native notion of what the number counts. A bare `weight` column would let somebody
average an interaction count against a declared closeness and get a number that is true of
neither.

**Provenance travels too, per node and per edge.** Invariant 5 exists so a view can tell an
enacted relation from a declared one; an export that flattened the two would hand a scholar
a graph asserting things the narrative never shows.

**Review is read over the document, not out of it.** Decisions live beside the snapshot and
supersede what it declared (**5.1**, **D50**), so an export that trusted the stored
`review_status` would publish a cast somebody has since rejected. Relations are the sharper
case: nothing in the pipeline writes a `review_status` onto an edge at all, so without the
overlay every edge would export as `proposed` forever, including the ones a person spent an
afternoon accepting.

**Evidence is in one export and counted in the other four.** The quotations are a nested
structure with locators and selectors, which GraphML, GEXF, and CSV can hold only as a
mangled string — so the graph formats carry `evidence_count` and nothing else, enough that a
claim with three passages behind it never reads as unevidenced. The passages themselves are
`annotations`, and there is exactly one place to look for them.

## What each graph format has room for

The four differ in how much they can say about the graph *as a whole*, and the export uses
whatever room each has rather than levelling down to the poorest:

- **GraphML** declares graph-scoped keys, so it carries the full provenance block — work,
  revision, run, model, prompt hash.
- **GEXF** has only `<meta>`, so it carries a one-line citation in the description. Gephi
  shows it on import.
- **CSV** has nowhere at all, so every row carries `snapshot_id` instead. Redundant per row,
  and the only thing that survives a spreadsheet being loaded, edited, and saved again.
- **JSON-LD** carries everything, and is the format to reach for when the export must be
  readable back as the document it came from.

## The word on a node

The schema's field is `name`; the flat formats export it as `label`, because that is the
word each of them gives to the string drawn on a node. A Gephi import where every node is
captioned `char:elizabeth-bennet` is the commonest way a correct export looks broken.
JSON-LD keeps `name`: it is not drawing anything.

## The two exports interlock

An annotation's body points at a claim by IRI, and that IRI is the one the graph export
gives the same claim — `identifier_prefixes` is shared rather than written twice, because a
citation pointing at an identifier the other file never mentions is a dangling reference
dressed up as provenance. Export both and they join up; export one and it stands alone.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from dramatis import __version__
from dramatis.schema import SCHEMA_FILENAME, load_schema
from dramatis.snapshot import canonical_json

GRAPHML = "graphml"
GEXF = "gexf"
CSV = "csv"
JSONLD = "jsonld"
ANNOTATIONS = "annotations"

GRAPH_FORMATS = (GRAPHML, GEXF, CSV, JSONLD)
"""The four that export the graph: nodes, edges, and what is needed to cite them (**6.1**)."""

FORMATS = (*GRAPH_FORMATS, ANNOTATIONS)
"""Every format `export_document` understands, in the order the CLI lists them.

`annotations` is not a fifth way of writing the graph. It exports the *evidence* (**6.2**),
which none of the other four carry, and it is the only one whose output contains a word of
the source text.
"""

LIST_SEPARATOR = "; "
"""How a multi-valued field is flattened for the formats that have no lists.

GEXF has a `liststring` type and GraphML has nothing, and readers disagree about which
delimiter a `liststring` uses. One separator across all three flat formats is worth more
than a per-format guess at what the reader will split on.
"""

CHARACTER = "character"
RELATION = "relation"

DRAMATIS_PREFIX = "dramatis"
"""The prefix everything outside a borrowed vocabulary is written under.

The Web Annotation export lives inside somebody else's context, where an unprefixed term
either means what that vocabulary says it means or is silently dropped. Anything Dramatis
adds there is prefixed, so a reader can tell at a glance which half of the document is
standard and which half is ours.
"""

MATCHING = "whitespace-collapsed"
"""How a `TextQuoteSelector` from Dramatis is meant to be matched, said out loud.

Invariant 3 defines *verbatim* against whitespace-normalised text — runs of whitespace
collapse, nothing else is altered — and `verification` compares both sides that way. So a
stored quotation is the model's, not the source's, and may differ from the file in nothing
but a line break. A consumer doing a byte-exact search would fail on most quotations in a
hard-wrapped novel and conclude the evidence was fabricated.

The Web Annotation vocabulary has no way to say this, so it is said in a prefixed term
rather than left to be discovered.
"""

GEXF_NAMESPACE = "http://www.gexf.net/1.2draft"
"""GEXF 1.2draft, not 1.3.

1.3 is the later specification and 1.2draft is what the ecosystem actually reads: it is
NetworkX's default output and every Gephi still in use imports it. The acceptance criterion
for this phase is that the graph opens in Gephi, so the version is chosen for readers rather
than for recency. Revisable once 1.3 is universal; the only change is a namespace string.
"""

GRAPHML_NAMESPACE = "http://graphml.graphdrawing.org/xmlns"

ID_BASE = "https://kestoralabs.co.uk/dramatis/id"
TERM_BASE = "https://kestoralabs.co.uk/dramatis/ns#"


class ExportError(Exception):
    """A document could not be exported. The message says why."""


@dataclass(frozen=True)
class Part:
    """One file of an export. Most formats are one part; CSV is two."""

    suffix: str
    """What the part is called, appended to the name the caller asked for."""

    media_type: str
    text: str


@dataclass(frozen=True)
class Export:
    """One document rendered into one format, as one or more named parts."""

    format: str
    parts: tuple[Part, ...]

    @property
    def single(self) -> Part | None:
        """The only part, where there is only one. None for a format that writes several."""
        return self.parts[0] if len(self.parts) == 1 else None


# -- fields ---------------------------------------------------------------------------
#
# One table per class of thing, shared by all four writers, so a field added for GraphML
# cannot be missing from CSV. The type names are GraphML's; GEXF's differ in one place and
# `_gexf_type` maps it.

GRAPH_FIELDS: tuple[tuple[str, str], ...] = (
    ("snapshot_id", "string"),
    ("snapshot_label", "string"),
    ("created_at", "string"),
    ("collection_id", "string"),
    ("collection_name", "string"),
    ("work_id", "string"),
    ("work_title", "string"),
    ("creator", "string"),
    ("language", "string"),
    ("edition", "string"),
    ("text_revision_id", "string"),
    ("text_revision_sha256", "string"),
    ("analysis_run_id", "string"),
    ("model", "string"),
    ("provider", "string"),
    ("prompt_version", "string"),
    ("prompt_sha256", "string"),
    ("schema_version", "string"),
    ("generator", "string"),
)

NODE_FIELDS: tuple[tuple[str, str], ...] = (
    ("label", "string"),
    ("kind", "string"),
    ("aliases", "string"),
    ("salience", "double"),
    ("confidence", "double"),
    ("provenance", "string"),
    ("review_status", "string"),
    ("notes", "string"),
    ("evidence_count", "int"),
)

EDGE_FIELDS: tuple[tuple[str, str], ...] = (
    ("weight", "double"),
    ("weight_basis", "string"),
    ("directed", "boolean"),
    ("types", "string"),
    ("valence", "double"),
    ("confidence", "double"),
    ("provenance", "string"),
    ("review_status", "string"),
    ("notes", "string"),
    ("evidence_count", "int"),
)

NODE_COLUMNS = ("snapshot_id", "id", *(name for name, _ in NODE_FIELDS))
EDGE_COLUMNS = ("snapshot_id", "id", "source", "target", *(name for name, _ in EDGE_FIELDS))


def _joined(values: Any) -> str | None:
    if not isinstance(values, list) or not values:
        return None
    return LIST_SEPARATOR.join(str(value) for value in values)


def _number(value: Any) -> str:
    """Render a number without inventing precision it has not got."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _flat(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _number(value)
    return str(value)


def _present(values: dict[str, Any]) -> dict[str, Any]:
    """Drop the fields the document said nothing about.

    An absent salience is not a salience of zero, and an absent confidence is not a low one —
    the distinction **D54** turns on. Every writer here omits rather than defaults.
    """
    return {key: value for key, value in values.items() if value is not None and value != ""}


def _status(
    review: Mapping[tuple[str, str], str] | None, kind: str, identifier: str, declared: Any
) -> Any:
    """The standing review status, falling back to whatever the document declared."""
    if review is not None:
        found = review.get((kind, identifier))
        if found is not None:
            return found
    return declared


def _graph_values(document: dict[str, Any]) -> dict[str, Any]:
    snapshot = document.get("snapshot") or {}
    work = (document.get("works") or [{}])[0]
    collection = document.get("collection") or {}
    revisions = {str(entry.get("id")): entry for entry in document.get("text_revisions") or []}
    runs = {str(entry.get("id")): entry for entry in document.get("analysis_runs") or []}

    revision = revisions.get(str(snapshot.get("text_revision_id"))) or {}
    run = runs.get(str(snapshot.get("analysis_run_id"))) or {}

    return _present(
        {
            "snapshot_id": snapshot.get("id"),
            "snapshot_label": snapshot.get("label"),
            "created_at": snapshot.get("created_at"),
            "collection_id": collection.get("id"),
            "collection_name": collection.get("name"),
            "work_id": work.get("id"),
            "work_title": work.get("title"),
            "creator": work.get("creator"),
            "language": work.get("language"),
            "edition": work.get("edition"),
            "text_revision_id": snapshot.get("text_revision_id"),
            "text_revision_sha256": revision.get("sha256"),
            "analysis_run_id": snapshot.get("analysis_run_id"),
            "model": run.get("model"),
            "provider": run.get("provider"),
            "prompt_version": run.get("prompt_version"),
            "prompt_sha256": run.get("prompt_sha256"),
            "schema_version": document.get("schema_version"),
            "generator": f"dramatis {__version__}",
        }
    )


def _node_values(
    character: dict[str, Any], review: Mapping[tuple[str, str], str] | None
) -> dict[str, Any]:
    identifier = str(character.get("id"))
    return _present(
        {
            "label": character.get("name"),
            "kind": character.get("kind"),
            "aliases": _joined(character.get("aliases")),
            "salience": character.get("salience"),
            "confidence": character.get("confidence"),
            "provenance": character.get("provenance"),
            "review_status": _status(review, CHARACTER, identifier, character.get("review_status")),
            "notes": character.get("notes"),
            "evidence_count": len(character.get("evidence") or []),
        }
    )


def _edge_values(
    relation: dict[str, Any], review: Mapping[tuple[str, str], str] | None
) -> dict[str, Any]:
    identifier = str(relation.get("id"))
    return _present(
        {
            "weight": relation.get("weight"),
            "weight_basis": relation.get("weight_basis"),
            # Always present, never omitted: the schema's default is false, and a reader that
            # met no value would have to know that to draw the edge the right way round.
            "directed": bool(relation.get("directed", False)),
            "types": _joined(relation.get("types")),
            "valence": relation.get("valence"),
            "confidence": relation.get("confidence"),
            "provenance": relation.get("provenance"),
            "review_status": _status(review, RELATION, identifier, relation.get("review_status")),
            "notes": relation.get("notes"),
            "evidence_count": len(relation.get("evidence") or []),
        }
    )


def citation(document: dict[str, Any]) -> str:
    """One line naming what this graph is a reading of, and by what.

    ASCII, and no wider than a sentence: it is what GEXF has room for and what Gephi shows
    in an import report. Both axes of Invariant 4 are in it, because a citation that named
    only the text would credit a changed reading to a rewrite.
    """
    values = _graph_values(document)
    title = values.get("work_title", "an untitled work")
    parts = [f"{title} - snapshot {values.get('snapshot_id', 'unknown')}"]
    if "text_revision_id" in values:
        parts.append(f"text {values['text_revision_id']}")
    if "analysis_run_id" in values:
        run = values["analysis_run_id"]
        model = values.get("model")
        parts.append(f"analysis {run}" + (f" ({model})" if model else ""))
    return f"{'; '.join(parts)}. Exported by dramatis {__version__}."


# -- GraphML --------------------------------------------------------------------------


def _key_id(scope: str, name: str) -> str:
    return f"{scope[0]}_{name}"


def _data(parent: ET.Element, scope: str, values: Mapping[str, Any]) -> None:
    for name, value in values.items():
        element = ET.SubElement(parent, "data", {"key": _key_id(scope, name)})
        element.text = _flat(value)


def _as_graphml(document: dict[str, Any], review: Mapping[tuple[str, str], str] | None) -> str:
    root = ET.Element(
        "graphml",
        {
            "xmlns": GRAPHML_NAMESPACE,
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": (f"{GRAPHML_NAMESPACE} {GRAPHML_NAMESPACE}/1.0/graphml.xsd"),
        },
    )

    for scope, fields in (("graph", GRAPH_FIELDS), ("node", NODE_FIELDS), ("edge", EDGE_FIELDS)):
        for name, kind in fields:
            # `directed` is an attribute of the edge element itself in GraphML, so declaring
            # a key for it too would put the same fact in two places that can disagree.
            if scope == "edge" and name == "directed":
                continue
            ET.SubElement(
                root,
                "key",
                {
                    "id": _key_id(scope, name),
                    "for": scope,
                    "attr.name": name,
                    "attr.type": kind,
                },
            )

    graph_values = _graph_values(document)
    graph = ET.SubElement(
        root,
        "graph",
        {"id": str(graph_values.get("snapshot_id", "graph")), "edgedefault": "undirected"},
    )
    # The DTD orders a graph's children: data before nodes and edges.
    _data(graph, "graph", graph_values)

    for character in document.get("characters") or []:
        node = ET.SubElement(graph, "node", {"id": str(character.get("id"))})
        _data(node, "node", _node_values(character, review))

    for relation in document.get("relations") or []:
        values = _edge_values(relation, review)
        attributes = {
            "id": str(relation.get("id")),
            "source": str(relation.get("source")),
            "target": str(relation.get("target")),
            "directed": "true" if values["directed"] else "false",
        }
        edge = ET.SubElement(graph, "edge", attributes)
        _data(edge, "edge", {k: v for k, v in values.items() if k != "directed"})

    return _serialise(root)


def _serialise(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


# -- GEXF -----------------------------------------------------------------------------


def _gexf_type(kind: str) -> str:
    """GraphML's type names, translated. Only the integer differs."""
    return "integer" if kind == "int" else kind


def _attvalues(parent: ET.Element, values: Mapping[str, Any]) -> None:
    if not values:
        return
    container = ET.SubElement(parent, "attvalues")
    for name, value in values.items():
        ET.SubElement(container, "attvalue", {"for": name, "value": _flat(value)})


def _as_gexf(document: dict[str, Any], review: Mapping[tuple[str, str], str] | None) -> str:
    root = ET.Element("gexf", {"xmlns": GEXF_NAMESPACE, "version": "1.2"})
    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "creator").text = f"dramatis {__version__}"
    ET.SubElement(meta, "description").text = citation(document)

    graph = ET.SubElement(root, "graph", {"mode": "static", "defaultedgetype": "undirected"})

    # `label` is a native attribute of a GEXF node and `weight` and the edge's direction are
    # native to an edge, so none of the three is declared here. The rest are.
    node_declared = tuple((name, kind) for name, kind in NODE_FIELDS if name != "label")
    edge_declared = tuple(
        (name, kind) for name, kind in EDGE_FIELDS if name not in ("weight", "directed")
    )
    for scope, fields in (("node", node_declared), ("edge", edge_declared)):
        attributes = ET.SubElement(graph, "attributes", {"class": scope})
        for name, kind in fields:
            ET.SubElement(
                attributes,
                "attribute",
                {"id": name, "title": name, "type": _gexf_type(kind)},
            )

    nodes = ET.SubElement(graph, "nodes")
    for character in document.get("characters") or []:
        values = _node_values(character, review)
        identifier = str(character.get("id"))
        node = ET.SubElement(
            nodes, "node", {"id": identifier, "label": str(values.get("label", identifier))}
        )
        _attvalues(node, {k: v for k, v in values.items() if k != "label"})

    edges = ET.SubElement(graph, "edges")
    for relation in document.get("relations") or []:
        values = _edge_values(relation, review)
        attributes = {
            "id": str(relation.get("id")),
            "source": str(relation.get("source")),
            "target": str(relation.get("target")),
            "type": "directed" if values["directed"] else "undirected",
        }
        if "weight" in values:
            attributes["weight"] = _flat(values["weight"])
        edge = ET.SubElement(edges, "edge", attributes)
        _attvalues(edge, {k: v for k, v in values.items() if k not in ("weight", "directed")})

    return _serialise(root)


# -- CSV ------------------------------------------------------------------------------


def _rows(columns: tuple[str, ...], entries: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    # `\n` rather than the RFC's `\r\n`: every reader accepts it, and it is the only choice
    # that makes an export byte-identical whichever platform produced it.
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for entry in entries:
        writer.writerow([_flat(entry[column]) if column in entry else "" for column in columns])
    return buffer.getvalue()


def _as_csv(
    document: dict[str, Any], review: Mapping[tuple[str, str], str] | None
) -> tuple[Part, ...]:
    snapshot_id = str((document.get("snapshot") or {}).get("id", ""))

    nodes = [
        {
            "snapshot_id": snapshot_id,
            "id": str(character.get("id")),
            **_node_values(character, review),
        }
        for character in document.get("characters") or []
    ]
    edges = [
        {
            "snapshot_id": snapshot_id,
            "id": str(relation.get("id")),
            "source": str(relation.get("source")),
            "target": str(relation.get("target")),
            **_edge_values(relation, review),
        }
        for relation in document.get("relations") or []
    ]

    return (
        Part(".nodes.csv", "text/csv", _rows(NODE_COLUMNS, nodes)),
        Part(".edges.csv", "text/csv", _rows(EDGE_COLUMNS, edges)),
    )


# -- JSON-LD --------------------------------------------------------------------------
#
# Dramatis identifiers were already namespaced by kind — `char:`, `rel:`, `work:` and the
# rest, because `ids.py` wanted a bare string in a stored document to be self-describing.
# That is exactly the shape of a JSON-LD compact IRI, so every identifier in the store is
# already a term and the context only has to say what each prefix expands to.
#
# Two prefixes cannot expand to the same place, and the split is the one `ids.py` already
# draws. A **content-derived** identifier means the same thing everywhere: `rev:abc123` is
# that text in this store and in any other, which is what makes two independently produced
# snapshots comparable. A **name-derived** identifier means something only inside the
# registry that minted it: `char:mary` is one person in one collection and a different
# person in another, so those expand under the collection they belong to. Flattening the
# two would merge every Mary in the world into one node the moment two exports met.

GLOBAL_PREFIXES = ("rev", "run", "snap", "doc")
SCOPED_PREFIXES = ("work", "char", "rel")


def identifier_prefixes(document: dict[str, Any]) -> dict[str, str]:
    """What each identifier prefix in this document expands to.

    Shared by both linked-data exports rather than built twice. The graph and the annotations
    describe the same characters and the same relations, and they are only usable together —
    an annotation whose body points at a claim the graph export gave a different IRI is a
    dangling reference dressed up as a citation.
    """
    collection_id = str((document.get("collection") or {}).get("id", "col:untitled"))
    scope = quote(collection_id.removeprefix("col:"), safe="")

    prefixes = {"col": f"{ID_BASE}/collection/"}
    for prefix in GLOBAL_PREFIXES:
        prefixes[prefix] = f"{ID_BASE}/{prefix}/"
    for prefix in SCOPED_PREFIXES:
        prefixes[prefix] = f"{ID_BASE}/collection/{scope}/{prefix}/"
    return prefixes


def expand_identifier(identifier: str, prefixes: Mapping[str, str]) -> str:
    """Resolve a Dramatis identifier to the IRI its prefix names.

    Unprefixed, or prefixed by something this document does not declare, it is left alone:
    inventing an expansion for an identifier of unknown kind would mint an IRI that means
    nothing.
    """
    prefix, separator, rest = identifier.partition(":")
    if not separator or prefix not in prefixes:
        return identifier
    return f"{prefixes[prefix]}{quote(rest, safe='')}"


def _context(document: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "@version": 1.1,
        "@vocab": TERM_BASE,
        "sdo": "https://schema.org/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "id": "@id",
        "type": "@type",
    }
    context.update(identifier_prefixes(document))

    context.update(
        {
            "name": "sdo:name",
            "title": "sdo:name",
            "creator": "sdo:creator",
            "language": "sdo:inLanguage",
            "created_at": {"@id": "sdo:dateCreated", "@type": "xsd:dateTime"},
            "conforms_to": {"@id": "sdo:schemaVersion", "@type": "@id"},
            "source": {"@id": f"{TERM_BASE}source", "@type": "@id"},
            "target": {"@id": f"{TERM_BASE}target", "@type": "@id"},
            # `works`, plural, and not `work`: `work` is a prefix here, and a term cannot be
            # both a prefix and a property without JSON-LD 1.1's `@prefix` escape hatch. The
            # plural is what the schema document calls the same field anyway.
            "works": {"@id": f"{TERM_BASE}work", "@container": "@set"},
            "work_id": {"@id": f"{TERM_BASE}work", "@type": "@id"},
            "document_ids": {"@id": f"{TERM_BASE}document", "@type": "@id", "@container": "@list"},
            "segment_types": {"@id": f"{TERM_BASE}segmentType", "@container": "@list"},
            "aliases": {"@id": f"{TERM_BASE}alias", "@container": "@set"},
            "types": {"@id": f"{TERM_BASE}relationType", "@container": "@set"},
            "characters": {"@id": f"{TERM_BASE}character", "@container": "@set"},
            "relations": {"@id": f"{TERM_BASE}relation", "@container": "@set"},
            "documents": {"@id": f"{TERM_BASE}document", "@container": "@set"},
            # Free-form by design, in the schema and here. `@json` keeps them readable
            # without expanding whatever keys somebody's provider happened to use into
            # predicates this vocabulary never defined.
            "parameters": {"@id": f"{TERM_BASE}parameters", "@type": "@json"},
            "attributes": {"@id": f"{TERM_BASE}attributes", "@type": "@json"},
        }
    )
    return context


def _as_jsonld(document: dict[str, Any], review: Mapping[tuple[str, str], str] | None) -> str:
    snapshot = document.get("snapshot") or {}
    work = (document.get("works") or [{}])[0]
    collection = document.get("collection") or {}
    revisions = {str(entry.get("id")): entry for entry in document.get("text_revisions") or []}
    runs = {str(entry.get("id")): entry for entry in document.get("analysis_runs") or []}

    revision = revisions.get(str(snapshot.get("text_revision_id"))) or {}
    run = runs.get(str(snapshot.get("analysis_run_id"))) or {}

    rendered: dict[str, Any] = {
        "@context": _context(document),
        "id": snapshot.get("id"),
        "type": "Snapshot",
        "schema_version": document.get("schema_version"),
        "conforms_to": load_schema().get("$id", SCHEMA_FILENAME),
        "generator": f"dramatis {__version__}",
    }
    if snapshot.get("label"):
        rendered["label"] = snapshot["label"]
    if snapshot.get("created_at"):
        rendered["created_at"] = snapshot["created_at"]

    rendered["collection"] = _present(
        {
            "id": collection.get("id"),
            "type": "Collection",
            "name": collection.get("name"),
            "description": collection.get("description"),
        }
    )
    rendered["works"] = [
        _present(
            {
                "id": work.get("id"),
                "type": "Work",
                "title": work.get("title"),
                "creator": work.get("creator"),
                "language": work.get("language"),
                "edition": work.get("edition"),
                "segment_types": work.get("segment_types"),
            }
        )
    ]
    rendered["text_revision"] = _present(
        {
            "id": snapshot.get("text_revision_id"),
            "type": "TextRevision",
            "label": revision.get("label"),
            "sha256": revision.get("sha256"),
            "created_at": revision.get("created_at"),
            "document_ids": revision.get("document_ids"),
        }
    )
    rendered["analysis_run"] = _present(
        {
            "id": snapshot.get("analysis_run_id"),
            "type": "AnalysisRun",
            "model": run.get("model"),
            "provider": run.get("provider"),
            "prompt_version": run.get("prompt_version"),
            "prompt_sha256": run.get("prompt_sha256"),
            "pipeline_version": run.get("pipeline_version"),
            "application_version": run.get("application_version"),
            "parameters": run.get("parameters") or None,
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
        }
    )

    documents = [
        _present(
            {
                "id": entry.get("id"),
                "type": "Document",
                "work_id": entry.get("work_id"),
                "title": entry.get("title"),
                "path": entry.get("path"),
                "role": entry.get("role"),
                "media_type": entry.get("media_type"),
                "sha256": entry.get("sha256"),
            }
        )
        for entry in document.get("documents") or []
    ]
    if documents:
        rendered["documents"] = documents

    rendered["characters"] = [
        _present(
            {
                "id": character.get("id"),
                "type": "Character",
                # `name` here, `label` in the flat formats: nothing in JSON-LD is drawing a
                # node, so the schema's own word is the right one.
                "name": character.get("name"),
                "aliases": character.get("aliases"),
                "kind": character.get("kind"),
                "salience": character.get("salience"),
                "confidence": character.get("confidence"),
                "provenance": character.get("provenance"),
                "review_status": _status(
                    review, CHARACTER, str(character.get("id")), character.get("review_status")
                ),
                "attributes": character.get("attributes") or None,
                "notes": character.get("notes"),
                "evidence_count": len(character.get("evidence") or []),
            }
        )
        for character in document.get("characters") or []
    ]

    rendered["relations"] = [
        _present(
            {
                "id": relation.get("id"),
                "type": "Relation",
                "source": relation.get("source"),
                "target": relation.get("target"),
                "directed": bool(relation.get("directed", False)),
                "weight": relation.get("weight"),
                "weight_basis": relation.get("weight_basis"),
                "types": relation.get("types"),
                "valence": relation.get("valence"),
                "confidence": relation.get("confidence"),
                "provenance": relation.get("provenance"),
                "review_status": _status(
                    review, RELATION, str(relation.get("id")), relation.get("review_status")
                ),
                "notes": relation.get("notes"),
                "evidence_count": len(relation.get("evidence") or []),
            }
        )
        for relation in document.get("relations") or []
    ]

    return json.dumps(rendered, indent=2, ensure_ascii=False) + "\n"


# -- W3C Web Annotation -------------------------------------------------------------
#
# The evidence export (**6.2**), and the other half of the answer D64 split in two: the four
# graph formats say a claim has three passages behind it, and this says which three.
#
# One annotation per piece of evidence. The target is the passage; the body is the claim it
# supports; the selector is a `TextQuoteSelector`, which is the whole reason this format was
# chosen — Dramatis already anchors evidence by quotation-with-context precisely so it
# survives an edit to the text (2.4), and the schema says as much where `selector` is
# defined. The formats meet where they were already standing.


ANNOTATION_CONTEXT = "http://www.w3.org/ns/anno.jsonld"

IDENTIFYING = "identifying"
DESCRIBING = "describing"
"""The two motivations, and neither is a perfect fit.

The Web Annotation vocabulary has no `evidencing`. `identifying` — *"the user intends to
assign an identity to the Target"* — is what a passage naming a character does; `describing`
is what a relation does to the passage that enacts it. Both are standard terms, which is the
point: a consumer that meets `dramatis:evidencing` ignores it, and one that meets
`identifying` knows what to do. Inventing a motivation would have been more accurate and
less useful.
"""


def _annotation_id(snapshot_id: str, claim_id: str, evidence: dict[str, Any]) -> str:
    """A stable identifier for one annotation, derived rather than minted.

    The schema allows evidence to carry an ``id`` and the pipeline does not write one, so
    there is nothing to carry through. Deriving it from the reading, the claim, and the
    quotation follows `ids.py`: export the same snapshot twice and the annotations have the
    same identifiers, which is what lets one be cited.

    Scoped to the snapshot on purpose. An annotation is a statement about a passage made by
    one reading, and two readings that both quote the same line have made that statement
    twice.
    """
    selector = evidence.get("selector") or {}
    material = canonical_json(
        [
            snapshot_id,
            claim_id,
            (evidence.get("locator") or {}).get("document_id"),
            (evidence.get("locator") or {}).get("path"),
            selector.get("exact"),
            selector.get("prefix"),
            selector.get("suffix"),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{ID_BASE}/annotation/{digest}"


def _claims_with_evidence(
    document: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """Every piece of evidence in the document, with the claim it belongs to.

    Characters first and then relations, each in document order, so two exports of one
    snapshot list their annotations the same way — the ordering `review.subjects` settles for
    the same reason.
    """
    names = {
        str(character.get("id")): str(character.get("name") or character.get("id"))
        for character in document.get("characters") or []
    }

    found: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for character in document.get("characters") or []:
        for evidence in character.get("evidence") or []:
            found.append((CHARACTER, names.get(str(character.get("id")), ""), character, evidence))

    for relation in document.get("relations") or []:
        source = names.get(str(relation.get("source")), str(relation.get("source")))
        target = names.get(str(relation.get("target")), str(relation.get("target")))
        joiner = "->" if relation.get("directed") else "--"
        for evidence in relation.get("evidence") or []:
            found.append((RELATION, f"{source} {joiner} {target}", relation, evidence))

    return found


def _annotation(
    kind: str,
    label: str,
    claim: dict[str, Any],
    evidence: dict[str, Any],
    *,
    snapshot: Mapping[str, Any],
    documents: Mapping[str, Any],
    prefixes: Mapping[str, str],
    review: Mapping[tuple[str, str], str] | None,
) -> dict[str, Any]:
    snapshot_id = str(snapshot.get("id", ""))
    claim_id = str(claim.get("id"))
    locator = evidence.get("locator") or {}
    selector = evidence.get("selector") or {}

    document_id = str(locator.get("document_id") or "")

    # A locator need not name a document — the schema makes it optional, and a single-file
    # work has nothing to disambiguate. The text revision is then what the quotation is a
    # quotation of, and it is always there, so the target still points at something. A
    # `SpecificResource` with no source at all would be a citation of nowhere.
    named = document_id or str(snapshot.get("text_revision_id") or "")
    source: dict[str, Any] = _present(
        {
            "id": expand_identifier(named, prefixes) if named else None,
            "type": "Text",
            f"{DRAMATIS_PREFIX}:documentId": document_id or None,
            f"{DRAMATIS_PREFIX}:sha256": (documents.get(document_id) or {}).get("sha256"),
        }
    )

    quote: dict[str, Any] = _present(
        {
            "type": "TextQuoteSelector",
            "exact": selector.get("exact"),
            "prefix": selector.get("prefix"),
            "suffix": selector.get("suffix"),
        }
    )

    target: dict[str, Any] = _present(
        {
            "type": "SpecificResource",
            "source": source,
            "selector": quote,
            # Invariant 1: structural position is an ordered path of typed segments whose
            # types are data. No standard selector addresses that, and flattening it to
            # "chapter 4" would bake in a vocabulary the schema refuses to have.
            f"{DRAMATIS_PREFIX}:locator": locator.get("path"),
            f"{DRAMATIS_PREFIX}:textRevision": snapshot.get("text_revision_id"),
            # Said out loud because a consumer cannot infer it and would otherwise fail on
            # every quotation crossing a line break. See MATCHING below.
            f"{DRAMATIS_PREFIX}:matching": MATCHING,
            # Deliberately not a `TextPositionSelector`. The schema calls these *"a hint for
            # fast lookup, never the authority"*, and they count characters into the
            # revision's **normalised** text — a string no consumer of this file has. Emitted
            # as a standard selector they would look authoritative and be wrong; dropped
            # entirely they would be lost. So they are carried, prefixed, and named after the
            # text they are offsets into.
            f"{DRAMATIS_PREFIX}:normalisedStart": selector.get("start"),
            f"{DRAMATIS_PREFIX}:normalisedEnd": selector.get("end"),
        }
    )

    body: list[dict[str, Any]] = [
        _present(
            {
                "id": expand_identifier(claim_id, prefixes),
                "type": f"{DRAMATIS_PREFIX}:{'Character' if kind == CHARACTER else 'Relation'}",
                "label": label or None,
                f"{DRAMATIS_PREFIX}:claimId": claim_id,
            }
        )
    ]
    if evidence.get("note"):
        # `commenting`, not part of the identification: the note says what the passage shows,
        # and is somebody's gloss rather than the claim itself.
        body.append(
            {
                "type": "TextualBody",
                "purpose": "commenting",
                "format": "text/plain",
                "value": str(evidence["note"]),
            }
        )
    if evidence.get("kind"):
        body.append(
            {
                "type": "TextualBody",
                "purpose": "classifying",
                "format": "text/plain",
                "value": str(evidence["kind"]),
            }
        )

    return _present(
        {
            "id": _annotation_id(snapshot_id, claim_id, evidence),
            "type": "Annotation",
            "motivation": IDENTIFYING if kind == CHARACTER else DESCRIBING,
            "created": snapshot.get("created_at"),
            "generator": {"type": "Software", "name": f"dramatis {__version__}"},
            "body": body[0] if len(body) == 1 else body,
            "target": target,
            f"{DRAMATIS_PREFIX}:provenance": claim.get("provenance"),
            f"{DRAMATIS_PREFIX}:reviewStatus": _status(
                review, kind, claim_id, claim.get("review_status")
            ),
            f"{DRAMATIS_PREFIX}:snapshot": snapshot.get("id"),
        }
    )


def _as_annotations(document: dict[str, Any], review: Mapping[tuple[str, str], str] | None) -> str:
    prefixes = identifier_prefixes(document)
    snapshot = document.get("snapshot") or {}
    snapshot_id = str(snapshot.get("id", ""))
    documents = {str(entry.get("id")): entry for entry in document.get("documents") or []}

    items = [
        _annotation(
            kind,
            label,
            claim,
            evidence,
            snapshot=snapshot,
            documents=documents,
            prefixes=prefixes,
            review=review,
        )
        for kind, label, claim, evidence in _claims_with_evidence(document)
    ]

    # An AnnotationCollection with its one page embedded, rather than a bare array. The array
    # is what everybody writes and the collection is what the specification defines; the
    # collection is also the only place a finite export has to say how many there are and
    # what they are a reading of.
    return (
        json.dumps(
            {
                "@context": [
                    ANNOTATION_CONTEXT,
                    {DRAMATIS_PREFIX: TERM_BASE, **prefixes},
                ],
                "id": f"{ID_BASE}/annotations/{quote(snapshot_id, safe='')}",
                "type": "AnnotationCollection",
                "label": citation(document),
                "total": len(items),
                "first": {
                    "id": f"{ID_BASE}/annotations/{quote(snapshot_id, safe='')}/1",
                    "type": "AnnotationPage",
                    "startIndex": 0,
                    "items": items,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


# -- the one entry point --------------------------------------------------------------


def export_document(
    document: dict[str, Any],
    fmt: str,
    *,
    review: Mapping[tuple[str, str], str] | None = None,
) -> Export:
    """Render a snapshot document into one interchange format.

    ``review`` maps ``(kind, id)`` to the standing review status, as `dramatis.review`
    reads it over the document. Omitted, the export carries whatever the snapshot declared —
    correct for a document that arrived from outside a store, and stale for one that did not.
    """
    if fmt not in FORMATS:
        offered = ", ".join(FORMATS)
        raise ExportError(f"unknown export format {fmt!r}; there is {offered}")

    if not isinstance(document.get("snapshot"), dict):
        raise ExportError("this is not a snapshot document: it has no snapshot")

    if fmt == CSV:
        return Export(CSV, _as_csv(document, review))
    if fmt == GRAPHML:
        text = _as_graphml(document, review)
        return Export(GRAPHML, (Part(".graphml", "application/graphml+xml", text),))
    if fmt == GEXF:
        text = _as_gexf(document, review)
        return Export(GEXF, (Part(".gexf", "application/gexf+xml", text),))

    if fmt == ANNOTATIONS:
        text = _as_annotations(document, review)
        return Export(
            ANNOTATIONS,
            (
                Part(
                    ".annotations.jsonld",
                    f'application/ld+json;profile="{ANNOTATION_CONTEXT}"',
                    text,
                ),
            ),
        )

    text = _as_jsonld(document, review)
    return Export(JSONLD, (Part(".jsonld", "application/ld+json", text),))

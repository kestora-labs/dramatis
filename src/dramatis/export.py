"""Getting a graph out of Dramatis and into somebody else's tool.

Four formats, because four different people want the same graph for four different reasons:
**GraphML** and **GEXF** for the network tools a digital humanist already has open,
**CSV** node and edge lists for the spreadsheet and the R session, and **JSON-LD** for the
catalogue that wants to link this graph to something else.

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

**Evidence is not in here, and 6.2 is why.** Every claim carries `evidence_count`, so
nothing silently reads as unevidenced, but the quotations themselves are a nested structure
with locators and selectors that GraphML, GEXF, and CSV can only hold as a mangled string.
**6.2** exports evidence as W3C Web Annotation, which is a format built for exactly that
shape. Cramming a lossy second copy into these four first would leave two answers to one
question.

## What each format has room for

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
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from dramatis import __version__
from dramatis.schema import SCHEMA_FILENAME, load_schema

GRAPHML = "graphml"
GEXF = "gexf"
CSV = "csv"
JSONLD = "jsonld"

FORMATS = (GRAPHML, GEXF, CSV, JSONLD)
"""Every format `export_document` understands, in the order the CLI lists them."""

LIST_SEPARATOR = "; "
"""How a multi-valued field is flattened for the formats that have no lists.

GEXF has a `liststring` type and GraphML has nothing, and readers disagree about which
delimiter a `liststring` uses. One separator across all three flat formats is worth more
than a per-format guess at what the reader will split on.
"""

CHARACTER = "character"
RELATION = "relation"

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


def _context(document: dict[str, Any]) -> dict[str, Any]:
    collection_id = str((document.get("collection") or {}).get("id", "col:untitled"))
    scope = quote(collection_id.removeprefix("col:"), safe="")

    context: dict[str, Any] = {
        "@version": 1.1,
        "@vocab": TERM_BASE,
        "sdo": "https://schema.org/",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "id": "@id",
        "type": "@type",
        "col": f"{ID_BASE}/collection/",
    }
    for prefix in GLOBAL_PREFIXES:
        context[prefix] = f"{ID_BASE}/{prefix}/"
    for prefix in SCOPED_PREFIXES:
        context[prefix] = f"{ID_BASE}/collection/{scope}/{prefix}/"

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

    text = _as_jsonld(document, review)
    return Export(JSONLD, (Part(".jsonld", "application/ld+json", text),))

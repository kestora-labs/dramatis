/**
 * Turning a selected node or edge into the fields a detail panel shows.
 *
 * The graph answers "who is near whom". This module answers the first half of "why is this
 * edge here" — what the snapshot actually claims about the thing under the cursor. The
 * supporting passages themselves are Phase 2.2; what is here is the claim and its
 * qualifiers.
 *
 * Three rules govern every field, and all three exist because the alternative misleads.
 *
 * **A weight is never shown without its basis.** Weights are comparable only within a
 * shared basis, so a bare `100` is a number with no unit. The panel renders the two
 * together or not at all.
 *
 * **An absent field is omitted, not rendered as blank or zero.** A snapshot that records
 * no confidence is not a snapshot with low confidence, and a relation with no types is not
 * an untyped relation — in both cases the run simply never said. Showing an empty row
 * would invent a claim the document does not make. This is not hypothetical: the
 * hand-authored fixture carries types, valence, confidence and notes, and a real model run
 * over a full novel currently carries none of them.
 *
 * **Free-text vocabulary is shown as it was stored.** Relation types, kinds and weight
 * bases are deliberately not enumerated by the schema, so there is no closed list to
 * prettify against. Tidying `interaction_passages` into "Interaction passages" would be
 * inventing a display convention the project has declined to fix.
 *
 * **Review status is not a field here.** It used to be, read straight from the document —
 * and from **5.1** that reading is stale the moment somebody rules on the claim, because a
 * decision is recorded beside the immutable snapshot rather than in it. It is served
 * separately, lives in `review.ts`, and the panel renders it as a control rather than as a
 * row, so there is one place showing where review stands instead of two disagreeing.
 */

import { listEvidence, type EvidenceEntry } from "./evidence.js";
import type { SnapshotCharacter, SnapshotDocument, SnapshotRelation } from "./graph.js";

/** Which list a selected element came from. */
export type SelectionKind = "character" | "relation";

/**
 * What the user has selected.
 *
 * The kind travels with the id because ids are unique within a kind and not across one —
 * the schema says so, and nothing forbids a character and a relation sharing a string.
 * Cytoscape already knows whether a node or an edge was clicked, so the caller passes that
 * along rather than this module guessing from an `id` prefix that is convention, not rule.
 */
export interface Selection {
  kind: SelectionKind;
  id: string;
}

export interface DetailField {
  label: string;
  value: string;
  /** An identifier or a stored vocabulary term, to be set in monospace rather than prose. */
  code?: boolean;
}

interface DetailBase {
  title: string;
  fields: DetailField[];
  notes?: string;
}

export interface CharacterDetail extends DetailBase {
  kind: "character";
  /** Other surface forms resolved to this character. */
  aliases: string[];
}

export interface RelationDetail extends DetailBase {
  kind: "relation";
  /** Free-text relation types as stored, e.g. "kinship", "antagonism". */
  types: string[];
  /** The supporting passages, in the order they occur in the work. */
  evidence: EvidenceEntry[];
}

export type Detail = CharacterDetail | RelationDetail;

/**
 * A number as the document meant it.
 *
 * Integers keep their form — a weight of 100 interaction passages is a count, and `100.00`
 * would dress a tally up as a measurement.
 */
export function decimal(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/**
 * A unit-interval value, always to two places.
 *
 * Unlike a count these are estimates, and `1` alongside `0.85` reads as a different kind
 * of quantity from its neighbour when it is the same one.
 */
export function unitInterval(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(2);
}

/**
 * Affective tone, with its sign kept.
 *
 * Valence runs -1 hostile to +1 affectionate, so the sign carries the meaning and a bare
 * `0.40` invites reading a mild warmth as a magnitude.
 */
export function valence(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

/** Add a field only when the document actually carries a value for it. */
function push(fields: DetailField[], label: string, value: string | undefined, code = false) {
  if (value === undefined) return;
  fields.push(code ? { label, value, code } : { label, value });
}

function optional<T>(value: T | undefined | null, format: (value: T) => string) {
  return value === undefined || value === null ? undefined : format(value);
}

/** How many relations this character takes part in, counting only drawable ones. */
function degreeOf(document: SnapshotDocument, id: string): number {
  const known = new Set(document.characters.map((character) => character.id));
  return document.relations.filter(
    (relation) =>
      known.has(relation.source) &&
      known.has(relation.target) &&
      (relation.source === id || relation.target === id),
  ).length;
}

export function describeCharacter(
  document: SnapshotDocument,
  character: SnapshotCharacter,
): CharacterDetail {
  const fields: DetailField[] = [];

  push(fields, "Kind", character.kind, true);
  // Degree earns a row because it is what node size encodes: the panel should explain the
  // picture, not restate it.
  push(fields, "Relations", String(degreeOf(document, character.id)));
  push(fields, "Salience", optional(character.salience, unitInterval));
  push(fields, "Confidence", optional(character.confidence, unitInterval));
  push(fields, "Provenance", character.provenance, true);
  push(
    fields,
    "Evidence",
    optional(character.evidence?.length, (count) => `${count} passages`),
  );
  push(fields, "Id", character.id, true);

  return {
    kind: "character",
    title: character.name,
    aliases: character.aliases ?? [],
    fields,
    notes: character.notes,
  };
}

export function describeRelation(
  document: SnapshotDocument,
  relation: SnapshotRelation,
): RelationDetail {
  const names = new Map(document.characters.map((character) => [character.id, character.name]));
  // An endpoint with no character is a document the validator would reject, and `buildGraph`
  // drops such an edge before it can be selected. Falling back to the raw id keeps this
  // function total rather than making the panel the place that discovers the problem.
  const source = names.get(relation.source) ?? relation.source;
  const target = names.get(relation.target) ?? relation.target;

  const fields: DetailField[] = [];

  // Weight and basis are one field because they are one quantity. Splitting them across
  // two rows invites reading the number alone.
  push(fields, "Weight", `${decimal(relation.weight)} ${relation.weight_basis}`);
  push(fields, "Valence", optional(relation.valence, valence));
  push(fields, "Confidence", optional(relation.confidence, unitInterval));
  push(fields, "Provenance", relation.provenance, true);
  // No "Evidence — n passages" row: the list below carries its own count, and a field
  // stating the length of a list printed underneath it is the same fact twice.
  push(fields, "Id", relation.id, true);

  return {
    kind: "relation",
    // Direction is carried by the join rather than by a "Directed: no" row on every
    // undirected edge, which is most of them.
    title: relation.directed ? `${source} → ${target}` : `${source} — ${target}`,
    types: relation.types ?? [],
    evidence: listEvidence(document, relation.evidence),
    fields,
    notes: relation.notes,
  };
}

/**
 * Describe whatever is selected, or nothing.
 *
 * Returns null when the selection names something this document does not contain, which
 * happens normally: switching snapshots leaves the previous selection pointing at an id
 * the new document may not have.
 */
export function describeSelection(
  document: SnapshotDocument,
  selection: Selection | null,
): Detail | null {
  if (!selection) return null;

  if (selection.kind === "character") {
    const character = document.characters.find((candidate) => candidate.id === selection.id);
    return character ? describeCharacter(document, character) : null;
  }

  const relation = document.relations.find((candidate) => candidate.id === selection.id);
  return relation ? describeRelation(document, relation) : null;
}

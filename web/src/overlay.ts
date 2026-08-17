/**
 * Drawing a diff: the graph with the change on it, and the change as a sentence.
 *
 * Two renderings of one comparison, because they answer different questions. The overlay
 * answers *where* — which part of the cast moved, and whether the movement is central or at
 * the edges. The list answers *what*, in an order a reader can go down and check. Neither
 * substitutes for the other: a graph cannot say "weight 4 to 25" and a list cannot show that
 * both edges that changed happen to meet at the same character.
 *
 * **The overlay is drawn over the union of both graphs, not over the later one.** A relation
 * that was removed does not exist in the newer snapshot, so drawing only what is there would
 * silently omit exactly the half of the diff a reader is least able to reconstruct — the
 * part that is gone. Removed elements are drawn and marked as removed.
 *
 * **Attribution travels with both renderings.** A change list that does not say whether the
 * text or the reading moved is the thing fixture B warns against before it lists a single
 * expected change, and the warning applies as much to a picture as to a paragraph.
 */

import type { SnapshotDocument, SnapshotRelation } from "./graph.js";

export type ChangeKind =
  "added" | "removed" | "merged" | "split" | "strengthened" | "weakened" | "retyped" | "unchanged";

export interface CharacterChange {
  id: string;
  name: string;
  kind: ChangeKind;
  counterparts: string[];
}

export interface RelationChange {
  id: string;
  source: string;
  target: string;
  kinds: ChangeKind[];
  weight_before: number | null;
  weight_after: number | null;
  delta: number | null;
  types_before: string[];
  types_after: string[];
}

/** What `GET /api/diff` returns. */
export interface DiffResponse {
  before: string;
  after: string;
  attribution: "text" | "analysis" | "both" | "same";
  weights_comparable: boolean;
  weight_basis: string | null;
  warnings: string[];
  characters: CharacterChange[];
  relations: RelationChange[];
}

/** A relation's identity for the overlay: its pair of endpoints, as the diff keys them. */
export function pairKey(source: string, target: string): string {
  return [source, target].sort().join(" ");
}

/**
 * One document holding everything either snapshot had.
 *
 * Built so the existing graph machinery can size and scale it exactly as it does a single
 * snapshot — the overlay should read as the same picture with marks on it, not as a second
 * kind of diagram with its own conventions.
 *
 * The later snapshot wins where both hold a character or relation, because the overlay is a
 * picture of where the work now stands, annotated with how it got there.
 */
export function unionDocument(before: SnapshotDocument, after: SnapshotDocument): SnapshotDocument {
  const characters = [...after.characters];
  const known = new Set(characters.map((character) => character.id));
  for (const character of before.characters) {
    if (!known.has(character.id)) characters.push(character);
  }

  const relations = [...after.relations];
  const pairs = new Set(relations.map((relation) => pairKey(relation.source, relation.target)));
  for (const relation of before.relations) {
    if (!pairs.has(pairKey(relation.source, relation.target))) relations.push(relation);
  }

  return { ...after, characters, relations };
}

/**
 * How each character and relation changed, keyed as the graph will find them.
 *
 * Relations are keyed by endpoint pair rather than by identifier, matching how the diff
 * decides two relations are the same edge: an identifier is derived from the names its
 * endpoints had at the time, so a merge would make one edge look like two.
 */
export function changeIndex(diff: DiffResponse): {
  characters: Map<string, ChangeKind>;
  relations: Map<string, ChangeKind[]>;
} {
  const characters = new Map<string, ChangeKind>();
  for (const change of diff.characters) characters.set(change.id, change.kind);

  const relations = new Map<string, ChangeKind[]>();
  for (const change of diff.relations) {
    relations.set(pairKey(change.source, change.target), change.kinds);
  }

  return { characters, relations };
}

/**
 * The class a drawn element carries, or nothing when it did not change.
 *
 * One class, not several: an edge that both strengthened and was retyped is drawn as the
 * change that moved it, because a picture cannot say two things about one line at once and
 * choosing silently would be worse than choosing by a stated rule. The list says both.
 */
export function classFor(kinds: ChangeKind[] | undefined): ChangeKind | null {
  if (!kinds || kinds.length === 0) return null;
  for (const kind of ["removed", "added", "weakened", "strengthened", "retyped"] as ChangeKind[]) {
    if (kinds.includes(kind)) return kind;
  }
  return kinds[0];
}

export interface ChangeEntry {
  kind: ChangeKind;
  subject: string;
  detail: string;
}

function nameOf(document: SnapshotDocument, id: string): string {
  return document.characters.find((character) => character.id === id)?.name ?? id;
}

function weight(value: number | null): string {
  if (value === null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/**
 * The diff as a list somebody can read down and check.
 *
 * Characters first, then relations, because a relation that arrived because a character
 * arrived is easier to read once the character has been accounted for. Within each, the
 * order is the order the diff reports, which is the order the documents hold them in —
 * stable between runs, and not a ranking the data does not support.
 */
export function changeList(union: SnapshotDocument, diff: DiffResponse): ChangeEntry[] {
  const entries: ChangeEntry[] = [];

  for (const change of diff.characters) {
    const others = change.counterparts.map((id) => nameOf(union, id)).join(", ");
    entries.push({
      kind: change.kind,
      subject: change.name || change.id,
      detail:
        change.kind === "merged"
          ? `merged into ${others || "another character"}`
          : change.kind === "split"
            ? `split out of ${others || "another character"}`
            : change.kind === "added"
              ? "appears for the first time"
              : "no longer appears",
    });
  }

  for (const change of diff.relations) {
    const subject = `${nameOf(union, change.source)} — ${nameOf(union, change.target)}`;
    const parts: string[] = [];

    if (change.kinds.includes("added")) parts.push(`new, at ${weight(change.weight_after)}`);
    if (change.kinds.includes("removed")) parts.push(`gone, was ${weight(change.weight_before)}`);
    if (change.kinds.includes("strengthened") || change.kinds.includes("weakened")) {
      parts.push(`${weight(change.weight_before)} to ${weight(change.weight_after)}`);
    }
    if (change.kinds.includes("retyped")) {
      const was = change.types_before.join(", ") || "untyped";
      const now = change.types_after.join(", ") || "untyped";
      parts.push(`${was} to ${now}`);
    }

    entries.push({
      kind: classFor(change.kinds) ?? "unchanged",
      subject,
      detail: parts.join("; "),
    });
  }

  return entries;
}

/**
 * What the diff can be laid at, said plainly.
 *
 * Returned for every attribution including the good ones, because this is the sentence that
 * decides what the rest of the screen is worth and it should not be present only when
 * something is wrong.
 */
export function describeAttribution(diff: DiffResponse): string {
  switch (diff.attribution) {
    case "text":
      return "The analysis was held still, so these changes belong to the text.";
    case "analysis":
      return "The text was held still, so these changes belong to the reading.";
    case "both":
      // Worded as `lineage.ts` words the same fact: one project, one sentence for it.
      return "The text and the analysis both changed, so neither can be credited with this.";
    case "same":
      return "The same text revision read by the same analysis.";
  }
}

/** Relations the diff did not mention, which is most of them in a healthy comparison. */
export function unchangedRelations(
  union: SnapshotDocument,
  diff: DiffResponse,
): SnapshotRelation[] {
  const changed = new Set(diff.relations.map((change) => pairKey(change.source, change.target)));
  return union.relations.filter(
    (relation) => !changed.has(pairKey(relation.source, relation.target)),
  );
}

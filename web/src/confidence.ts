/**
 * How sure a reading was, and how to draw an edge it was not sure of.
 *
 * The schema has carried `confidence` on nodes and edges since **0.3**, and until now nothing
 * looked at it. Four rules decide what this does with it, and each exists because the obvious
 * alternative misleads.
 *
 * **An absent confidence is not a low one.** A snapshot that records no confidence is not a
 * snapshot with no confidence in itself — the run simply never said. `detail.ts` has drawn
 * that distinction for fields in the panel since **2.1**; drawing every unqualified edge as
 * uncertain would be the same mistake made in ink, across a whole graph, where it cannot be
 * argued with. An edge with nothing recorded is drawn exactly as it is drawn today.
 *
 * **Low is below the midpoint, and the midpoint is the only number available.** The schema
 * declares confidence as a value from 0 to 1 and says nothing about what it counts — there is
 * no `confidence_basis` the way there is a `weight_basis`. So any threshold is a reading of an
 * undeclared scale, and 0.5 is the one that needs no tuning: it is the point at which a
 * reading stops being more sure than not. The number is stated on screen rather than applied
 * silently, because a reader who disagrees with it needs to know it was applied.
 *
 * **The mark is dotted, because dashed is spoken for.** The diff draws a removed edge dashed
 * and **4.4** draws a declared-but-never-enacted edge dashed, both for the reason recorded
 * there: *a dashed edge is the one convention a reader already reads as "not really there"*.
 * A third meaning on the same mark would make all three unreadable.
 *
 * **An overlay outranks this.** A diff and a provenance comparison each answer a question the
 * reader asked for, and confidence is a standing property of the graph. Where both apply the
 * overlay wins, which the stylesheet arranges by order rather than by anything here.
 */

import type { SnapshotCharacter, SnapshotDocument, SnapshotRelation } from "./graph.js";

/**
 * Below this, a reading was more unsure than sure, and the edge is marked.
 *
 * Not tuned, and deliberately not tunable: see the note above. If confidence ever acquires a
 * declared basis, this becomes a decision with evidence behind it rather than the midpoint of
 * an interval, and it should move then.
 */
export const UNCERTAIN_BELOW = 0.5;

/** The recorded confidence of a node or edge, or null where the reading did not say. */
export function confidenceOf(entry: { confidence?: number } | undefined | null): number | null {
  const value = entry?.confidence;
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  // Outside the interval the schema declares, the value is not on the scale the threshold
  // reads, so it is treated as unsaid rather than clamped into a claim nobody made.
  if (value < 0 || value > 1) return null;
  return value;
}

/** Whether this is an element a reading recorded *and* was unsure of. */
export function isUncertain(entry: { confidence?: number } | undefined | null): boolean {
  const value = confidenceOf(entry);
  return value !== null && value < UNCERTAIN_BELOW;
}

export interface Confidence {
  /** How many relations the reading recorded a confidence for. */
  recorded: number;
  /** How many relations there are, recorded or not. */
  relations: number;
  /** How many of the recorded ones fall below the threshold. */
  uncertain: number;
  /** The least confident value recorded, or null where none was. */
  lowest: number | null;
}

export function summarise(document: SnapshotDocument | null): Confidence {
  const relations: SnapshotRelation[] = document?.relations ?? [];
  const values = relations
    .map((relation) => confidenceOf(relation))
    .filter((value): value is number => value !== null);

  return {
    recorded: values.length,
    relations: relations.length,
    uncertain: values.filter((value) => value < UNCERTAIN_BELOW).length,
    lowest: values.length > 0 ? Math.min(...values) : null,
  };
}

/** Whether there is anything here worth explaining an encoding for. */
export function isRecorded(summary: Confidence): boolean {
  return summary.recorded > 0;
}

/**
 * What the sidebar says about it.
 *
 * A reading that records none says so. That is the useful answer and the common one: nothing
 * in this application asks a model for a confidence, so every graph Dramatis has produced so
 * far carries none. Leaving the row out entirely would let a reader take an unqualified graph
 * for a confident one, which is the question they are most likely to be asking.
 */
export function describe(summary: Confidence): string {
  if (!isRecorded(summary)) return "not recorded by this reading";

  const scope =
    summary.recorded === summary.relations
      ? `${summary.recorded} relation(s)`
      : `${summary.recorded} of ${summary.relations} relation(s)`;

  if (summary.uncertain === 0) return `${scope}, none below ${UNCERTAIN_BELOW.toFixed(2)}`;
  return `${scope}, ${summary.uncertain} below ${UNCERTAIN_BELOW.toFixed(2)}`;
}

/** The legend line, for a snapshot that has an encoding to explain. */
export function legend(summary: Confidence): string | null {
  if (!isRecorded(summary)) return null;
  return (
    `Dotted edges are the ones this reading was less than ${UNCERTAIN_BELOW.toFixed(2)} sure ` +
    "of. An edge with no confidence recorded is drawn solid: the run never said, which is not " +
    "the same as saying it was unsure."
  );
}

/** Nodes and edges the reading was unsure of, for anything listing them. */
export function uncertainOf(document: SnapshotDocument | null): {
  characters: SnapshotCharacter[];
  relations: SnapshotRelation[];
} {
  return {
    characters: (document?.characters ?? []).filter((character) => isUncertain(character)),
    relations: (document?.relations ?? []).filter((relation) => isUncertain(relation)),
  };
}

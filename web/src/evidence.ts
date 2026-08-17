/**
 * Putting a relation's supporting passages in the order they occur in the work.
 *
 * The panel from 2.1 says a relation is worth weight 46. This is where a reader finds out
 * why, and reading them in narrative order is the difference between a list of quotations
 * and an account of how a relationship went.
 *
 * **Nothing upstream guarantees the order.** Today's snapshots happen to arrive sorted,
 * because extraction is a map-reduce over segments taken in order. That is a property of
 * the current pipeline, not of the format: the schema imposes no ordering on an `evidence`
 * array, aggregation may merge relations drawn from separate windows, and 2.4 re-anchors
 * quotations against an edited text, which is precisely an operation that moves them. So
 * the view sorts rather than trusting what it was handed.
 *
 * **Where position is unknown, it is not invented.** A locator may omit `index`, a work may
 * have several documents while a locator names none, and two passages may sit in the same
 * segment with nothing to separate them. In each case the comparison yields a tie and the
 * sort is stable, so the stored order survives. The alternative — ordering on `type`
 * alphabetically, or on the quotation itself — would produce a confident sequence that
 * means nothing.
 */

import type { SnapshotDocument, SnapshotEvidence, SnapshotSegment } from "./graph.js";

/** Where the documents of a work sit relative to each other, by id. */
export function documentOrder(document: SnapshotDocument): Map<string, number> {
  return new Map((document.documents ?? []).map((entry, position) => [entry.id, position]));
}

/**
 * Rank a locator's document.
 *
 * Evidence naming no document, or one the snapshot does not list, sorts after evidence that
 * names a known one — an unplaceable passage cannot be interleaved with placeable ones. In
 * the common case of a single-document work where no locator names it, every piece ranks
 * the same and this has no effect.
 */
function documentRank(evidence: SnapshotEvidence, order: Map<string, number>): number {
  const id = evidence.locator.document_id;
  if (id === undefined) return Number.POSITIVE_INFINITY;
  return order.get(id) ?? Number.POSITIVE_INFINITY;
}

/**
 * Compare two structural paths, outermost segment first.
 *
 * A segment carrying an index precedes one that does not, at the same depth: a known
 * ordinal is placeable and a missing one is not. Segment `type` is deliberately not
 * compared — types are supplied per work and are data, so alphabetising them would impose
 * an order the project has never claimed.
 */
function comparePaths(a: SnapshotEvidence, b: SnapshotEvidence): number {
  const left = a.locator.path;
  const right = b.locator.path;

  for (let depth = 0; depth < Math.min(left.length, right.length); depth += 1) {
    const here = left[depth].index;
    const there = right[depth].index;

    if (here === undefined && there === undefined) continue;
    if (here === undefined) return 1;
    if (there === undefined) return -1;
    if (here !== there) return here - there;
  }

  // One path is a prefix of the other: the container precedes what it contains.
  return left.length - right.length;
}

/**
 * Order two pieces of evidence by where they fall in the work.
 *
 * Returns 0 when the two cannot be told apart, which is a real answer rather than a failure
 * — see the note on stability above.
 */
export function comparePosition(
  a: SnapshotEvidence,
  b: SnapshotEvidence,
  order: Map<string, number>,
): number {
  const fromA = documentRank(a, order);
  const fromB = documentRank(b, order);
  if (fromA !== fromB) {
    // Subtracting would give -Infinity here, which is a direction but not a comparator.
    if (!Number.isFinite(fromA)) return 1;
    if (!Number.isFinite(fromB)) return -1;
    return fromA - fromB;
  }

  const byPath = comparePaths(a, b);
  if (byPath !== 0) return byPath;

  // The offset is documented as a hint and never the authority, which is exactly what a
  // last-resort tie-break is. It is consulted only once structure has run out.
  const here = a.selector.start;
  const there = b.selector.start;
  if (here !== undefined && there !== undefined) return here - there;

  return 0;
}

/** A relation's evidence, in the order it occurs in the work. */
export function orderEvidence(
  document: SnapshotDocument,
  evidence: SnapshotEvidence[],
): SnapshotEvidence[] {
  const order = documentOrder(document);
  // Array.prototype.sort is stable, so ties keep the order the snapshot stored them in.
  return [...evidence].sort((a, b) => comparePosition(a, b, order));
}

/**
 * A locator as a reader would say it: "chapter 3", "part 2 › chapter 7".
 *
 * The segment type is printed as the work declared it rather than mapped onto a display
 * vocabulary, for the reason the schema gives for keeping it free text: no closed list of
 * structural names survives contact with a real corpus.
 */
export function formatPath(path: SnapshotSegment[]): string {
  return path
    .map((segment) => {
      const named = segment.index === undefined ? segment.type : `${segment.type} ${segment.index}`;
      return segment.label === undefined ? named : `${named} — ${segment.label}`;
    })
    .join(" › ");
}

export function formatLocator(evidence: SnapshotEvidence): string {
  return formatPath(evidence.locator.path);
}

/** One supporting passage, ready to render. */
export interface EvidenceEntry {
  /**
   * Where this piece sits in the *stored* array, which is how the server addresses it.
   *
   * Not its place in this list. The list is sorted into reading order, so the two differ,
   * and asking the server for "the third piece" of a re-ordered list would open the wrong
   * passage. Carrying the stored index also keeps the quotation out of the request.
   */
  position: number;
  /** Structural position, e.g. "chapter 3". */
  locator: string;
  /** The document this passage came from, named only when the work has more than one. */
  document?: string;
  /** The quotation, verbatim as stored. */
  quotation: string;
  /** What this passage shows about the claim. */
  note?: string;
}

/**
 * Everything the evidence list needs, in reading order.
 *
 * The document is named only when a work has more than one, because repeating a single
 * title against every passage is noise that hides the position beside it.
 */
export function listEvidence(
  document: SnapshotDocument,
  evidence: SnapshotEvidence[] | undefined,
): EvidenceEntry[] {
  if (!evidence || evidence.length === 0) return [];

  const documents = document.documents ?? [];
  const titles = new Map(documents.map((entry) => [entry.id, entry.title ?? entry.id]));
  const many = documents.length > 1;
  // Fixed before sorting, so each entry keeps the address the server knows it by.
  const stored = new Map(evidence.map((piece, position) => [piece, position]));

  return orderEvidence(document, evidence).map((piece) => {
    const from = piece.locator.document_id;
    const entry: EvidenceEntry = {
      position: stored.get(piece) ?? 0,
      locator: formatLocator(piece),
      quotation: piece.selector.exact,
    };
    if (many && from !== undefined) entry.document = titles.get(from) ?? from;
    if (piece.note !== undefined) entry.note = piece.note;
    return entry;
  });
}

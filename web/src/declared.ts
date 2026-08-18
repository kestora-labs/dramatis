/**
 * What the corpus says about itself, against what it actually does.
 *
 * Phase **4.3** made reference material and narrative two readings producing two kinds of
 * edge. This is the view they were separated for. Fixture **C** names the two findings it
 * exists to surface, before it lists anything else:
 *
 * > **Declared but never enacted.** The bible states that Ada Mbeki and Tomas Reiner are
 * > estranged siblings — a relationship given a whole section. They never share a scene.
 * > This is the gap an author most wants surfaced: a relationship that exists in the plan
 * > and not on the page.
 * >
 * > **Enacted but never declared.** Ada and Sister Yeong carry the most page time of any
 * > pair in the corpus, and the bible does not mention the relationship at all.
 *
 * Three rules shape what follows, and each of them is a way this view could have lied.
 *
 * **Pairs are matched by their endpoints, never by relation id.** 4.3 deliberately made the
 * identifiers differ by provenance so the two classes could not merge upstream. Matching on
 * id here would therefore find agreement nowhere, and report an entire corpus as both
 * undeclared and unenacted.
 *
 * **Nothing compares the weights.** An observed weight counts passages of contact and an
 * asserted weight counts statements; `require_comparable` refuses to put them on one scale,
 * and this view honours that rather than working around it. The question here is only
 * whether a pair appears on each side. *Declared more strongly than enacted* is not a
 * sentence this data can support, so it is not one the view offers.
 *
 * **The view is withheld unless both classes are present.** In a narrative-only corpus every
 * relation is trivially enacted-but-never-declared, which is not a finding but a restatement
 * of the corpus having no bible. Offering it anyway would be **2.5**'s failure again: a
 * control that looks like information and is not.
 */

import type { GraphElement, Provenance, SnapshotDocument, SnapshotRelation } from "./graph.js";
import { pairKey } from "./overlay.js";

/** Where a pair stands between what was declared and what was enacted. */
export type Agreement = "declared-only" | "enacted-only" | "agreed";

export interface PairComparison {
  key: string;
  source: string;
  target: string;
  agreement: Agreement;
  /** The declaring edge and the enacting one. Exactly one is null unless they agree. */
  asserted: SnapshotRelation | null;
  observed: SnapshotRelation | null;
  /** What the reference material called the relationship. Empty when nothing declared it. */
  types: string[];
}

export interface Comparison {
  pairs: PairComparison[];
  declaredOnly: PairComparison[];
  enactedOnly: PairComparison[];
  agreed: PairComparison[];
  /**
   * Whether this snapshot can be asked the question at all.
   *
   * False when it holds only one of the two classes, where every answer would be the same
   * answer and none of them a finding.
   */
  available: boolean;
  /** Why not, in a sentence a reader is shown instead of a control they cannot use. */
  unavailable: string | null;
  /**
   * Relations that are neither declared nor enacted — `human`, per Invariant 5.
   *
   * Counted rather than dropped or folded into a side. A relation somebody entered by hand
   * is not evidence about the corpus disagreeing with itself, but silently omitting it would
   * make the totals here disagree with the totals everywhere else in the application.
   */
  other: SnapshotRelation[];
}

const DECLARING: Provenance = "asserted";
const ENACTING: Provenance = "observed";

/** Compare what a snapshot declares against what it enacts. */
export function compareProvenance(document: SnapshotDocument): Comparison {
  const relations = document.relations ?? [];
  const asserted = relations.filter((relation) => relation.provenance === DECLARING);
  const observed = relations.filter((relation) => relation.provenance === ENACTING);
  const other = relations.filter(
    (relation) => relation.provenance !== DECLARING && relation.provenance !== ENACTING,
  );

  if (asserted.length === 0 || observed.length === 0) {
    // Named precisely, because "unavailable" on its own reads as a defect. Which half is
    // missing tells a reader what their corpus would need for the question to mean anything.
    const missing =
      asserted.length === 0 && observed.length === 0
        ? "holds no declared or enacted relations"
        : asserted.length === 0
          ? "has no reference material declaring anything, so every relation is enacted only"
          : "has no narrative relations, so nothing in it has been enacted";
    return {
      pairs: [],
      declaredOnly: [],
      enactedOnly: [],
      agreed: [],
      available: false,
      unavailable: `This snapshot ${missing}.`,
      other,
    };
  }

  const byPair = new Map<string, PairComparison>();

  const put = (relation: SnapshotRelation, side: "asserted" | "observed") => {
    const key = pairKey(relation.source, relation.target);
    const existing = byPair.get(key);
    if (existing) {
      // Two edges of one side on one pair means a malformed snapshot. Keeping the first is
      // arbitrary but stable, and the pair is still counted exactly once either way.
      if (!existing[side]) existing[side] = relation;
      return;
    }
    const [source, target] = [relation.source, relation.target].sort();
    byPair.set(key, {
      key,
      source,
      target,
      agreement: "agreed",
      asserted: side === "asserted" ? relation : null,
      observed: side === "observed" ? relation : null,
      types: [],
    });
  };

  for (const relation of asserted) put(relation, "asserted");
  for (const relation of observed) put(relation, "observed");

  const pairs = [...byPair.values()].map((pair) => ({
    ...pair,
    agreement: (pair.asserted && pair.observed
      ? "agreed"
      : pair.asserted
        ? "declared-only"
        : "enacted-only") as Agreement,
    types: pair.asserted?.types ?? [],
  }));

  // Declared-only first: a relationship in the plan and not on the page is the finding an
  // author came here for. Agreements last, because they are the ones needing no attention.
  const rank: Record<Agreement, number> = { "declared-only": 0, "enacted-only": 1, agreed: 2 };
  pairs.sort((a, b) => rank[a.agreement] - rank[b.agreement] || a.key.localeCompare(b.key));

  return {
    pairs,
    declaredOnly: pairs.filter((pair) => pair.agreement === "declared-only"),
    enactedOnly: pairs.filter((pair) => pair.agreement === "enacted-only"),
    agreed: pairs.filter((pair) => pair.agreement === "agreed"),
    available: true,
    unavailable: null,
    other,
  };
}

/**
 * The class to mark each relation with, by relation id.
 *
 * Keyed by id rather than by pair because it is the *edges* that get drawn, and a pair the
 * two classes agree on has two of them.
 */
export function marksFor(comparison: Comparison): Map<string, Agreement> {
  const marks = new Map<string, Agreement>();
  for (const pair of comparison.pairs) {
    if (pair.asserted) marks.set(pair.asserted.id, pair.agreement);
    if (pair.observed) marks.set(pair.observed.id, pair.agreement);
  }
  return marks;
}

/**
 * Mark the built graph with the comparison.
 *
 * Lives here rather than in the component because **3.4** shipped its overlay with the marks
 * computed where they could not be tested, and they were silently never applied: the effect
 * building the graph did not depend on the diff, so the classes arrived after the elements
 * had already been handed to Cytoscape. Keeping the application beside the comparison means
 * a test can hold the two together.
 *
 * Mutates in place, matching how the caller already assembles element classes, and leaves
 * anything the comparison did not cover untouched — a character node, or a hand-entered
 * relation that is neither declared nor enacted.
 */
export function applyMarks(elements: GraphElement[], comparison: Comparison): GraphElement[] {
  const marks = marksFor(comparison);
  for (const element of elements) {
    const mark = marks.get(String(element.data.id));
    if (mark) element.classes = `${element.classes ?? ""} ${mark}`.trim();
  }
  return elements;
}

export interface FindingEntry {
  agreement: Agreement;
  subject: string;
  detail: string | null;
}

/**
 * The comparison as lines somebody can read down and check.
 *
 * Beside the marked graph rather than instead of it, for the reason **3.4** gives of its own
 * two renderings: a graph cannot say *estrangement, kinship*, and a list cannot show that
 * three of the undeclared pairs all meet at the same character.
 */
export function findingList(document: SnapshotDocument, comparison: Comparison): FindingEntry[] {
  const names = new Map(document.characters.map((character) => [character.id, character.name]));
  const nameOf = (id: string) => names.get(id) ?? id;

  return comparison.pairs.map((pair) => ({
    agreement: pair.agreement,
    subject: `${nameOf(pair.source)} and ${nameOf(pair.target)}`,
    detail: pair.types.length > 0 ? pair.types.join(", ") : null,
  }));
}

/** One sentence saying what the comparison found, for the head of the panel. */
export function describeComparison(comparison: Comparison): string {
  if (!comparison.available) return comparison.unavailable ?? "";

  const declared = comparison.declaredOnly.length;
  const enacted = comparison.enactedOnly.length;
  if (declared === 0 && enacted === 0) {
    return `Every relation is both declared and enacted (${comparison.agreed.length}).`;
  }

  const parts: string[] = [];
  if (declared > 0) parts.push(`${declared} declared but never enacted`);
  if (enacted > 0) parts.push(`${enacted} enacted but never declared`);
  return `${parts.join(", ")}; ${comparison.agreed.length} agreed.`;
}

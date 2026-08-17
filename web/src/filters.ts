/**
 * Narrowing the graph to the relations a reader is asking about.
 *
 * Three filters, and all three are about relations: a minimum weight, a set of relation
 * types, and a set of provenances. Characters are not filtered directly — a character is in
 * the picture if any of its relations is.
 *
 * **A filter is offered only when the snapshot gives it something to distinguish.** The
 * first full-novel run records no relation types at all and exactly one provenance across
 * all 241 relations, so a type control would be empty and a provenance control would offer
 * a single choice that changes nothing. Both would suggest the graph can be narrowed in
 * ways it cannot, which is the same failure as D25's blank confidence row: a control that
 * looks like information and is not. The hand-authored fixture, by contrast, carries eight
 * types, and there the controls are worth having.
 *
 * **The weight filter is refused when weights are not comparable.** Weights mean something
 * only against a shared basis. A snapshot mixing two has no single scale for "at least 20"
 * to be measured on, so the control is withheld rather than offered over a quantity that
 * does not exist — the same rule that stops 2.1 printing a weight without its basis.
 */

import type { Provenance, SnapshotDocument, SnapshotRelation } from "./graph.js";

export interface Filters {
  /** Relations below this weight are not drawn. */
  minimumWeight: number;
  /** Relation types to keep. Empty means every type, and untyped relations too. */
  types: string[];
  /** Provenances to keep. Empty means all of them. */
  provenance: Provenance[];
}

export const NO_FILTERS: Filters = { minimumWeight: 0, types: [], provenance: [] };

/** Whether anything is actually being narrowed. */
export function isNarrowed(filters: Filters): boolean {
  return filters.minimumWeight > 0 || filters.types.length > 0 || filters.provenance.length > 0;
}

/** What this snapshot can usefully be filtered by. */
export interface FilterOptions {
  /** Relation types present, in the order a reader would scan them: commonest first. */
  types: string[];
  /** Provenances present, offered only when there is more than one to choose between. */
  provenance: Provenance[];
  minWeight: number;
  maxWeight: number;
  /**
   * Whether a minimum-weight control means anything here.
   *
   * False when the relations disagree about their weight basis — there is no single scale
   * to set a floor on — and false when every weight is the same, where a floor either keeps
   * everything or removes everything.
   */
  weightUsable: boolean;
  /** The shared basis, to name the units on the control. Null when they disagree. */
  weightBasis: string | null;
}

export function optionsFor(document: SnapshotDocument): FilterOptions {
  const relations = document.relations ?? [];

  const counts = new Map<string, number>();
  for (const relation of relations) {
    for (const type of relation.types ?? []) counts.set(type, (counts.get(type) ?? 0) + 1);
  }
  // Commonest first, then alphabetically so the order is stable between snapshots rather
  // than depending on which relation happened to be extracted first.
  const types = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type]) => type);

  const seen = new Set(relations.map((relation) => relation.provenance));
  const order: Provenance[] = ["observed", "asserted", "human"];
  const provenance = seen.size > 1 ? order.filter((value) => seen.has(value)) : [];

  const bases = new Set(relations.map((relation) => relation.weight_basis));
  const weights = relations.map((relation) => relation.weight);
  const minWeight = weights.length > 0 ? Math.min(...weights) : 0;
  const maxWeight = weights.length > 0 ? Math.max(...weights) : 0;

  return {
    types,
    provenance,
    minWeight,
    maxWeight,
    weightUsable: bases.size === 1 && maxWeight > minWeight,
    weightBasis: bases.size === 1 ? [...bases][0] : null,
  };
}

/**
 * Whether a relation survives the filters.
 *
 * An empty list means "no opinion" rather than "nothing", so the default filters keep
 * everything. A relation carrying no types is kept by an empty type filter and dropped by
 * any non-empty one: asked for the kinship edges, a reader is not asking for the edges
 * nobody typed.
 */
export function passes(relation: SnapshotRelation, filters: Filters): boolean {
  if (relation.weight < filters.minimumWeight) return false;

  if (filters.types.length > 0) {
    const types = relation.types ?? [];
    if (!types.some((type) => filters.types.includes(type))) return false;
  }

  if (filters.provenance.length > 0 && !filters.provenance.includes(relation.provenance)) {
    return false;
  }

  return true;
}

/** Add or remove one value from a filter's list, for a control that toggles. */
export function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value)
    ? values.filter((candidate) => candidate !== value)
    : [...values, value];
}

/**
 * Turning a stored snapshot document into something Cytoscape can draw.
 *
 * The document arrives exactly as it was archived, so this is the only place that decides
 * how a graph *looks*. Two scaling choices carry the weight, and both exist because the
 * obvious alternative reads badly on real books.
 *
 * **Edge width is on a square-root scale.** Interaction counts are heavily skewed: two
 * protagonists share dozens of passages while most pairs share one or two. On a linear
 * scale the leads render as ropes and everyone else as hairlines indistinguishable from
 * each other, which hides exactly the mid-weight structure a reader is looking for.
 *
 * **Node size is on the same scale, by degree.** For the same reason, and so the two
 * encodings read consistently rather than one compressing while the other does not.
 */

import { NO_FILTERS, passes, type Filters } from "./filters.js";

export type Provenance = "observed" | "asserted" | "human";

export interface SnapshotCharacter {
  id: string;
  name: string;
  aliases?: string[];
  kind?: string;
  salience?: number;
  confidence?: number;
  provenance: Provenance;
  review_status?: string;
  notes?: string;
  evidence?: SnapshotEvidence[];
}

export interface SnapshotSegment {
  type: string;
  index?: number;
  label?: string;
}

export interface SnapshotEvidence {
  /** `start` and `end` are offsets into the normalised text: a hint, never the authority. */
  selector: { exact: string; prefix?: string; suffix?: string; start?: number; end?: number };
  locator: { document_id?: string; path: SnapshotSegment[] };
  note?: string;
  kind?: string;
}

export interface SnapshotRelation {
  id: string;
  source: string;
  target: string;
  weight: number;
  weight_basis: string;
  directed?: boolean;
  types?: string[];
  valence?: number;
  confidence?: number;
  provenance: Provenance;
  review_status?: string;
  notes?: string;
  evidence?: SnapshotEvidence[];
}

export interface SnapshotDocument {
  schema_version: string;
  collection: { id: string; name: string };
  works: { id: string; title: string; creator?: string }[];
  /** In corpus order, which is the order evidence from different documents reads in. */
  documents?: { id: string; work_id: string; title?: string; role: string }[];
  analysis_runs: { id: string; model: string; prompt_version: string }[];
  snapshot: { id: string; text_revision_id: string; analysis_run_id: string; label?: string };
  characters: SnapshotCharacter[];
  relations: SnapshotRelation[];
}

/**
 * What a width and a size are measured against.
 *
 * **absolute** — the heaviest relation in the snapshot, whether or not it is currently
 * drawn. A weight of 10 renders at one width and keeps it: narrowing the graph, or opening
 * a comparison, leaves every surviving edge exactly as thick as it was.
 *
 * **relative** — the heaviest relation *on screen*. The picture always uses its full range,
 * which is what a reader wants when studying one filtered view closely, and which is why
 * the option exists at all.
 *
 * The default is absolute because the relative case has a failure the reader cannot see.
 * Filter away the heaviest edge and every remaining edge thickens; nothing about the work
 * changed, but the graph now says the survivors are more central than it said a moment ago.
 * The same happens across a comparison whenever one snapshot is busier than the other: the
 * normalisation moves, the picture moves, and only the totals actually did.
 */
export type Scaling = "absolute" | "relative";

export const DEFAULT_SCALING: Scaling = "absolute";

export const EDGE_WIDTH = { min: 1, max: 14 } as const;
export const NODE_SIZE = { min: 18, max: 72 } as const;

/**
 * Map a value onto a range on a square-root scale.
 *
 * A zero maximum yields the minimum rather than dividing by zero: a graph where every
 * weight is zero has no relative structure to show, and every edge should look the same
 * rather than every edge disappearing.
 */
export function sqrtScale(
  value: number,
  maximum: number,
  range: { min: number; max: number },
): number {
  if (!Number.isFinite(value) || value <= 0 || maximum <= 0) return range.min;
  const ratio = Math.min(value / maximum, 1);
  return range.min + (range.max - range.min) * Math.sqrt(ratio);
}

export function edgeWidth(weight: number, maxWeight: number): number {
  return sqrtScale(weight, maxWeight, EDGE_WIDTH);
}

export function nodeSize(degree: number, maxDegree: number): number {
  return sqrtScale(degree, maxDegree, NODE_SIZE);
}

/** How many relations each character takes part in. */
export function degrees(document: SnapshotDocument): Map<string, number> {
  const counts = new Map<string, number>();
  for (const character of document.characters) counts.set(character.id, 0);
  for (const relation of document.relations) {
    for (const endpoint of [relation.source, relation.target]) {
      counts.set(endpoint, (counts.get(endpoint) ?? 0) + 1);
    }
  }
  return counts;
}

export interface GraphElement {
  data: Record<string, unknown>;
  classes?: string;
}

export interface BuiltGraph {
  elements: GraphElement[];
  /** The weight the widths were measured against, which depends on the scaling.
   *
   * The largest across every basis present. When a snapshot mixes bases, widths are measured
   * per basis instead — see `maxWeightByBasis` — and this is the number a reader would see
   * quoted for the heaviest edge overall.
   */
  maxWeight: number;
  /** The heaviest weight of each basis present, which is what widths are scaled against. */
  maxWeightByBasis: Record<string, number>;
  maxDegree: number;
  scaling: Scaling;
  weightBasis: string | null;
  /** How many relations are drawn, and how many the snapshot holds. */
  relationsShown: number;
  relationsTotal: number;
  /**
   * Characters left out because every relation they took part in was filtered away.
   *
   * Counted rather than silently dropped. A character with no relations *at all* is not in
   * this number — it is still drawn, dimmed, because having none is a fact about the
   * snapshot rather than a consequence of the filter.
   */
  charactersHidden: number;
}

/**
 * Build Cytoscape elements from a snapshot document.
 *
 * Relations whose endpoints are missing from `characters` are skipped rather than drawn
 * against a phantom node. The validator rejects such a document, so seeing one here means
 * something upstream is wrong — dropping the edge keeps the view honest instead of
 * inventing a node to hang it on.
 */
export function buildGraph(
  document: SnapshotDocument,
  filters: Filters = NO_FILTERS,
  scaling: Scaling = DEFAULT_SCALING,
): BuiltGraph {
  const known = new Set(document.characters.map((character) => character.id));
  const drawable = document.relations.filter(
    (relation) => known.has(relation.source) && known.has(relation.target),
  );
  const relations = drawable.filter((relation) => passes(relation, filters));

  const bases = new Set(relations.map((relation) => relation.weight_basis));
  // Weights are comparable only within a shared basis. A view mixing two would be a chart
  // that looks right and means nothing, so the caller is told rather than shown one.
  const weightBasis = bases.size === 1 ? [...bases][0] : null;

  const counts = degrees({ ...document, relations });
  // Degrees in the unfiltered graph, to tell a character the filter emptied from one that
  // the snapshot itself left with nobody.
  const unfiltered = degrees({ ...document, relations: drawable });
  // What the encodings are measured against. Under absolute scaling this is the whole
  // snapshot rather than the drawn subset, so a filter removes edges without resizing the
  // ones it leaves — the two encodings take their reference from the same place, as the
  // note at the top of this file requires of them.
  const measured = scaling === "absolute" ? drawable : relations;
  const against = scaling === "absolute" ? unfiltered : counts;
  const maxWeight = measured.reduce((most, relation) => Math.max(most, relation.weight), 0);

  // Widths are measured per basis, not across all of them. Phase 4.3 made a mixed snapshot
  // reachable for the first time: reference material yields relations weighted in
  // statements while narrative yields relations weighted in passages of contact, and a pair
  // stated once by a bible is not a twentieth as close as a pair sharing twenty scenes. One
  // maximum over both would draw exactly that claim. Within a basis the comparison is real,
  // so each edge is scaled against the heaviest edge that shares its basis.
  const maxByBasis = new Map<string, number>();
  for (const relation of measured) {
    const most = maxByBasis.get(relation.weight_basis) ?? 0;
    if (relation.weight > most) maxByBasis.set(relation.weight_basis, relation.weight);
  }
  const maxDegree = [...against.values()].reduce((most, count) => Math.max(most, count), 0);

  const elements: GraphElement[] = [];
  let charactersHidden = 0;

  for (const character of document.characters) {
    const degree = counts.get(character.id) ?? 0;

    // A character whose every relation was filtered away is not part of the picture being
    // asked for, and drawing a hundred dimmed dots hides the structure the filter was
    // applied to reveal. It is counted so the sidebar can say how many went.
    if (degree === 0 && (unfiltered.get(character.id) ?? 0) > 0) {
      charactersHidden += 1;
      continue;
    }

    elements.push({
      data: {
        id: character.id,
        label: character.name,
        aliases: character.aliases ?? [],
        kind: character.kind ?? "unknown",
        provenance: character.provenance,
        degree,
        size: nodeSize(degree, maxDegree),
      },
      classes: degree === 0 ? "isolated" : undefined,
    });
  }

  for (const relation of relations) {
    elements.push({
      data: {
        id: relation.id,
        source: relation.source,
        target: relation.target,
        weight: relation.weight,
        weightBasis: relation.weight_basis,
        provenance: relation.provenance,
        evidenceCount: relation.evidence?.length ?? 0,
        width: edgeWidth(relation.weight, maxByBasis.get(relation.weight_basis) ?? maxWeight),
      },
    });
  }

  return {
    elements,
    maxWeight,
    maxWeightByBasis: Object.fromEntries(maxByBasis),
    maxDegree,
    scaling,
    weightBasis,
    relationsShown: relations.length,
    relationsTotal: drawable.length,
    charactersHidden,
  };
}

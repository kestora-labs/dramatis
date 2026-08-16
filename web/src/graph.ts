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

export type Provenance = "observed" | "asserted" | "human";

export interface SnapshotCharacter {
  id: string;
  name: string;
  aliases?: string[];
  kind?: string;
  provenance: Provenance;
  review_status?: string;
}

export interface SnapshotEvidence {
  selector: { exact: string; prefix?: string; suffix?: string };
  locator: { document_id?: string; path: { type: string; index?: number; label?: string }[] };
  note?: string;
}

export interface SnapshotRelation {
  id: string;
  source: string;
  target: string;
  weight: number;
  weight_basis: string;
  directed?: boolean;
  provenance: Provenance;
  evidence?: SnapshotEvidence[];
}

export interface SnapshotDocument {
  schema_version: string;
  collection: { id: string; name: string };
  works: { id: string; title: string; creator?: string }[];
  analysis_runs: { id: string; model: string; prompt_version: string }[];
  snapshot: { id: string; text_revision_id: string; analysis_run_id: string; label?: string };
  characters: SnapshotCharacter[];
  relations: SnapshotRelation[];
}

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
  maxWeight: number;
  maxDegree: number;
  weightBasis: string | null;
}

/**
 * Build Cytoscape elements from a snapshot document.
 *
 * Relations whose endpoints are missing from `characters` are skipped rather than drawn
 * against a phantom node. The validator rejects such a document, so seeing one here means
 * something upstream is wrong — dropping the edge keeps the view honest instead of
 * inventing a node to hang it on.
 */
export function buildGraph(document: SnapshotDocument): BuiltGraph {
  const known = new Set(document.characters.map((character) => character.id));
  const relations = document.relations.filter(
    (relation) => known.has(relation.source) && known.has(relation.target),
  );

  const bases = new Set(relations.map((relation) => relation.weight_basis));
  // Weights are comparable only within a shared basis. A view mixing two would be a chart
  // that looks right and means nothing, so the caller is told rather than shown one.
  const weightBasis = bases.size === 1 ? [...bases][0] : null;

  const counts = degrees({ ...document, relations });
  const maxWeight = relations.reduce((most, relation) => Math.max(most, relation.weight), 0);
  const maxDegree = [...counts.values()].reduce((most, count) => Math.max(most, count), 0);

  const elements: GraphElement[] = [];

  for (const character of document.characters) {
    const degree = counts.get(character.id) ?? 0;
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
        width: edgeWidth(relation.weight, maxWeight),
      },
    });
  }

  return { elements, maxWeight, maxDegree, weightBasis };
}

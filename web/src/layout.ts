/**
 * Choosing how the graph is arranged, and keeping an arrangement once it is worth keeping.
 *
 * A force layout is a simulation with no memory. Run it twice on the same graph and the
 * same characters land somewhere else, which means a reader who spent a minute working out
 * where everyone is loses that the moment anything is redrawn — a filter moved, a snapshot
 * reloaded, the page refreshed. Pinning is the answer: the positions are recorded, and from
 * then on the graph opens as it was left.
 *
 * **A pin does not have to cover everything.** Filters change which characters are drawn, so
 * a pin taken while narrowed knows nothing about the ones that were hidden. Rather than
 * refusing, or silently rearranging the whole graph and destroying the pin, the nodes that
 * have positions are placed and the layout is run over only the ones that do not. A reader's
 * arrangement survives; the newcomers are fitted around it.
 *
 * **Where a pin is kept is a real decision, recorded in D30.** It lives in the browser, not
 * in the project file. `dramatis serve` reads and never writes — that is a stated property
 * of a tool whose users hold unpublished manuscripts — and adding a write endpoint so a
 * graph can remember where it put Mr Collins is not a trade this bullet is entitled to make.
 * The cost is that a pin does not travel with the project file, which is the right thing to
 * revisit when phase 6 needs a figure somebody else can reproduce.
 */

export type LayoutName = "cose" | "circle" | "concentric" | "grid" | "breadthfirst";

export interface LayoutChoice {
  name: LayoutName;
  label: string;
  /** What it is for, in the terms a reader of a novel would use. */
  note: string;
}

/**
 * The layouts on offer.
 *
 * All of them ship inside Cytoscape. An extension would be a new dependency for a bullet
 * about arranging what is already there, and the built-in set already spans the useful
 * range: a simulation that finds clusters, two that impose a shape, and two that impose an
 * order.
 */
export const LAYOUTS: LayoutChoice[] = [
  { name: "cose", label: "Force", note: "Clusters characters who share relations." },
  { name: "circle", label: "Circle", note: "Everyone on one ring, in the stored order." },
  { name: "concentric", label: "Concentric", note: "Rings by how many relations each has." },
  { name: "breadthfirst", label: "Hierarchy", note: "Layered outward from the busiest." },
  { name: "grid", label: "Grid", note: "Even rows, ignoring structure entirely." },
];

export const DEFAULT_LAYOUT: LayoutName = "cose";

export function isLayoutName(value: unknown): value is LayoutName {
  return LAYOUTS.some((choice) => choice.name === value);
}

export interface Position {
  x: number;
  y: number;
}

export type Positions = Record<string, Position>;

export interface PinnedLayout {
  layout: LayoutName;
  positions: Positions;
  /** When it was pinned, so the sidebar can say. */
  savedAt: string;
}

/** The slice of `localStorage` this module needs, so tests need no browser. */
export interface Storage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** Pins are per snapshot: two snapshots of one work are two different graphs. */
export function pinKey(snapshotId: string): string {
  return `dramatis.layout.${snapshotId}`;
}

function isPosition(value: unknown): value is Position {
  const point = value as Position;
  return (
    typeof point === "object" &&
    point !== null &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y)
  );
}

/**
 * Read a pin, or nothing.
 *
 * Anything unreadable is treated as absent rather than repaired. This is a convenience that
 * an old version of the client, a half-finished write, or a person with the developer tools
 * open may have left behind; the cost of ignoring it is one relayout, and the cost of
 * trusting it is characters stacked on top of each other at coordinates that mean nothing.
 */
export function loadPin(storage: Storage, snapshotId: string): PinnedLayout | null {
  let raw: string | null = null;
  try {
    raw = storage.getItem(pinKey(snapshotId));
  } catch {
    return null; // storage disabled, private window, quota policy — all "no pin"
  }
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as PinnedLayout;
    if (!isLayoutName(parsed?.layout)) return null;
    if (typeof parsed.positions !== "object" || parsed.positions === null) return null;

    const positions: Positions = {};
    for (const [id, point] of Object.entries(parsed.positions)) {
      if (isPosition(point)) positions[id] = { x: point.x, y: point.y };
    }
    if (Object.keys(positions).length === 0) return null;

    return { layout: parsed.layout, positions, savedAt: parsed.savedAt ?? "" };
  } catch {
    return null;
  }
}

export function savePin(storage: Storage, snapshotId: string, pin: PinnedLayout): void {
  try {
    storage.setItem(pinKey(snapshotId), JSON.stringify(pin));
  } catch {
    // A full or disabled store means the arrangement is not kept. Nothing else breaks, and
    // failing the whole view over a convenience would be the wrong trade.
  }
}

export function clearPin(storage: Storage, snapshotId: string): void {
  try {
    storage.removeItem(pinKey(snapshotId));
  } catch {
    // As above.
  }
}

export interface LayoutPlan {
  /** Nodes to place exactly, by id. Empty when nothing is pinned. */
  preset: Positions;
  /** Nodes with no pinned position, which the algorithm must place. */
  unplaced: string[];
  /** The algorithm to run over `unplaced`, or over everything when nothing is pinned. */
  algorithm: LayoutName;
  /** True when every drawn node has a pinned position and no algorithm need run at all. */
  complete: boolean;
}

/**
 * Decide how to arrange the nodes about to be drawn.
 *
 * `drawn` is the set of node ids in the graph as filtered. A pin covering all of them means
 * no layout runs, which is what makes a filter change cheap once an arrangement is settled —
 * the force layout costs the better part of a second on a full novel.
 */
export function planLayout(
  pin: PinnedLayout | null,
  drawn: string[],
  chosen: LayoutName = DEFAULT_LAYOUT,
): LayoutPlan {
  if (!pin) {
    return { preset: {}, unplaced: [...drawn], algorithm: chosen, complete: false };
  }

  const preset: Positions = {};
  const unplaced: string[] = [];
  for (const id of drawn) {
    const point = pin.positions[id];
    if (point) preset[id] = point;
    else unplaced.push(id);
  }

  return {
    preset,
    unplaced,
    // A pin records the layout it was taken under, so the newcomers are arranged the way
    // their neighbours were rather than by whatever the control happens to be showing.
    algorithm: pin.layout,
    complete: unplaced.length === 0,
  };
}

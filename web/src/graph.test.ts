import { describe, expect, it } from "vitest";

import {
  EDGE_WIDTH,
  NODE_SIZE,
  buildGraph,
  degrees,
  edgeWidth,
  nodeSize,
  sqrtScale,
  type SnapshotDocument,
} from "./graph.js";

function aDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "extract-v1" }],
    snapshot: { id: "snap:1", text_revision_id: "rev:1", analysis_run_id: "run:1" },
    characters: [
      { id: "char:a", name: "Ada", provenance: "observed" },
      { id: "char:b", name: "Bram", provenance: "observed" },
      { id: "char:c", name: "Cai", provenance: "observed" },
    ],
    relations: [
      {
        id: "rel:a--b",
        source: "char:a",
        target: "char:b",
        weight: 100,
        weight_basis: "interaction_passages",
        provenance: "observed",
      },
      {
        id: "rel:a--c",
        source: "char:a",
        target: "char:c",
        weight: 1,
        weight_basis: "interaction_passages",
        provenance: "observed",
      },
    ],
    ...overrides,
  };
}

describe("sqrtScale", () => {
  it("returns the minimum at zero and the maximum at the top", () => {
    expect(sqrtScale(0, 100, { min: 1, max: 10 })).toBe(1);
    expect(sqrtScale(100, 100, { min: 1, max: 10 })).toBe(10);
  });

  it("keeps a small value visible where a linear scale would not", () => {
    // The point of the square root: 1 in 100 is 10% of the range, not 1%.
    const linear = 1 + (10 - 1) * (1 / 100);
    const rooted = sqrtScale(1, 100, { min: 1, max: 10 });

    expect(rooted).toBeGreaterThan(linear);
    expect(rooted).toBeCloseTo(1.9, 5);
  });

  it("compresses the top of the range so the leads do not swamp everyone", () => {
    const half = sqrtScale(50, 100, { min: 0, max: 10 });
    expect(half).toBeCloseTo(7.07, 2);
  });

  it("treats a zero maximum as no structure to show, not a division by zero", () => {
    expect(sqrtScale(0, 0, { min: 2, max: 9 })).toBe(2);
    expect(Number.isFinite(sqrtScale(5, 0, { min: 2, max: 9 }))).toBe(true);
  });

  it("never exceeds the range", () => {
    expect(sqrtScale(1000, 100, { min: 1, max: 10 })).toBe(10);
  });

  it("survives nonsense input", () => {
    expect(sqrtScale(NaN, 100, { min: 1, max: 10 })).toBe(1);
    expect(sqrtScale(-5, 100, { min: 1, max: 10 })).toBe(1);
  });
});

describe("degrees", () => {
  it("counts the relations each character takes part in", () => {
    const counts = degrees(aDocument());

    expect(counts.get("char:a")).toBe(2);
    expect(counts.get("char:b")).toBe(1);
    expect(counts.get("char:c")).toBe(1);
  });

  it("gives a character with no relations a degree of zero", () => {
    const counts = degrees(aDocument({ relations: [] }));
    expect([...counts.values()]).toEqual([0, 0, 0]);
  });
});

describe("buildGraph", () => {
  it("emits a node per character and an edge per relation", () => {
    const { elements } = buildGraph(aDocument());
    const nodes = elements.filter((element) => !("source" in element.data));
    const edges = elements.filter((element) => "source" in element.data);

    expect(nodes).toHaveLength(3);
    expect(edges).toHaveLength(2);
  });

  it("scales edge width by weight", () => {
    const { elements } = buildGraph(aDocument());
    const heavy = elements.find((element) => element.data.id === "rel:a--b");
    const light = elements.find((element) => element.data.id === "rel:a--c");

    expect(heavy?.data.width).toBe(EDGE_WIDTH.max);
    expect(light?.data.width).toBeGreaterThan(EDGE_WIDTH.min);
    expect(light?.data.width).toBeLessThan(EDGE_WIDTH.max / 2);
  });

  it("scales node size by degree", () => {
    const { elements } = buildGraph(aDocument());
    const central = elements.find((element) => element.data.id === "char:a");
    const peripheral = elements.find((element) => element.data.id === "char:b");

    expect(central?.data.size).toBe(NODE_SIZE.max);
    expect(peripheral?.data.size).toBeLessThan(NODE_SIZE.max);
    expect(peripheral?.data.size).toBeGreaterThanOrEqual(NODE_SIZE.min);
  });

  it("marks a character with no relations", () => {
    const { elements } = buildGraph(aDocument({ relations: [] }));

    expect(elements.every((element) => element.classes === "isolated")).toBe(true);
  });

  it("reports the shared weight basis", () => {
    expect(buildGraph(aDocument()).weightBasis).toBe("interaction_passages");
  });

  it("reports no basis when the relations disagree", () => {
    // Weights on different bases are different quantities wearing the same name; a view
    // that averaged them would look right and mean nothing.
    const document = aDocument();
    document.relations[1].weight_basis = "words_exchanged";

    expect(buildGraph(document).weightBasis).toBeNull();
  });

  it("drops an edge whose endpoint is not a character", () => {
    const document = aDocument();
    document.relations.push({
      id: "rel:ghost",
      source: "char:a",
      target: "char:missing",
      weight: 5,
      weight_basis: "interaction_passages",
      provenance: "observed",
    });

    const { elements } = buildGraph(document);
    expect(elements.some((element) => element.data.id === "rel:ghost")).toBe(false);
    expect(elements.some((element) => element.data.id === "char:missing")).toBe(false);
  });

  it("does not let a dropped edge inflate a degree", () => {
    const document = aDocument();
    document.relations.push({
      id: "rel:ghost",
      source: "char:b",
      target: "char:missing",
      weight: 5,
      weight_basis: "interaction_passages",
      provenance: "observed",
    });

    const { elements } = buildGraph(document);
    const bram = elements.find((element) => element.data.id === "char:b");
    expect(bram?.data.degree).toBe(1);
  });

  it("carries evidence counts through for the detail panel", () => {
    const document = aDocument();
    document.relations[0].evidence = [
      { selector: { exact: "one" }, locator: { path: [{ type: "chapter", index: 1 }] } },
      { selector: { exact: "two" }, locator: { path: [{ type: "chapter", index: 2 }] } },
    ];

    const { elements } = buildGraph(document);
    const edge = elements.find((element) => element.data.id === "rel:a--b");
    expect(edge?.data.evidenceCount).toBe(2);
  });

  it("handles an empty graph", () => {
    const { elements, maxWeight, maxDegree } = buildGraph(
      aDocument({ characters: [], relations: [] }),
    );

    expect(elements).toEqual([]);
    expect(maxWeight).toBe(0);
    expect(maxDegree).toBe(0);
  });
});

describe("edgeWidth and nodeSize", () => {
  it("stay inside their declared ranges", () => {
    for (const weight of [0, 1, 7, 500]) {
      const width = edgeWidth(weight, 100);
      expect(width).toBeGreaterThanOrEqual(EDGE_WIDTH.min);
      expect(width).toBeLessThanOrEqual(EDGE_WIDTH.max);
    }
    for (const degree of [0, 1, 7, 500]) {
      const size = nodeSize(degree, 100);
      expect(size).toBeGreaterThanOrEqual(NODE_SIZE.min);
      expect(size).toBeLessThanOrEqual(NODE_SIZE.max);
    }
  });
});

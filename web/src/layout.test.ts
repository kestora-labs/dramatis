import { describe, expect, it } from "vitest";

import {
  DEFAULT_LAYOUT,
  LAYOUTS,
  clearPin,
  isLayoutName,
  loadPin,
  pinKey,
  planLayout,
  savePin,
  type PinnedLayout,
  type Storage,
} from "./layout.js";

/** A stand-in for `localStorage`, so these tests need no browser. */
function aStorage(seed: Record<string, string> = {}): Storage & { map: Map<string, string> } {
  const map = new Map(Object.entries(seed));
  return {
    map,
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
    removeItem: (key) => void map.delete(key),
  };
}

/** A storage that refuses everything, as a private window or a full quota does. */
function aRefusingStorage(): Storage {
  return {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("quota");
    },
    removeItem() {
      throw new Error("denied");
    },
  };
}

const aPin: PinnedLayout = {
  layout: "cose",
  positions: { "char:a": { x: 10, y: 20 }, "char:b": { x: 30, y: 40 } },
  savedAt: "2026-08-17T00:00:00Z",
};

describe("the layouts on offer", () => {
  it("names only layouts that ship inside Cytoscape", () => {
    // An extension would be a new dependency for a bullet about arranging what is there.
    const builtIn = ["cose", "circle", "concentric", "breadthfirst", "grid"];
    for (const choice of LAYOUTS) expect(builtIn).toContain(choice.name);
  });

  it("gives every layout a label and a note saying what it is for", () => {
    for (const choice of LAYOUTS) {
      expect(choice.label, choice.name).not.toBe("");
      expect(choice.note, choice.name).not.toBe("");
    }
  });

  it("offers the default among them", () => {
    expect(isLayoutName(DEFAULT_LAYOUT)).toBe(true);
  });

  it("rejects a name it does not know", () => {
    expect(isLayoutName("dagre")).toBe(false);
    expect(isLayoutName(undefined)).toBe(false);
    expect(isLayoutName(42)).toBe(false);
  });
});

describe("keeping a pin", () => {
  it("round-trips a pin through storage", () => {
    const storage = aStorage();
    savePin(storage, "snap:1", aPin);

    expect(loadPin(storage, "snap:1")).toEqual(aPin);
  });

  it("keeps one pin per snapshot", () => {
    // Two snapshots of one work are two different graphs; sharing an arrangement would
    // place characters at coordinates belonging to a different analysis.
    const storage = aStorage();
    savePin(storage, "snap:1", aPin);

    expect(loadPin(storage, "snap:2")).toBeNull();
    expect(storage.map.has(pinKey("snap:1"))).toBe(true);
  });

  it("forgets a pin when it is cleared", () => {
    const storage = aStorage();
    savePin(storage, "snap:1", aPin);
    clearPin(storage, "snap:1");

    expect(loadPin(storage, "snap:1")).toBeNull();
  });

  it("has no pin when nothing was ever saved", () => {
    expect(loadPin(aStorage(), "snap:1")).toBeNull();
  });
});

describe("reading a pin that cannot be trusted", () => {
  it("treats unparseable content as no pin", () => {
    expect(loadPin(aStorage({ [pinKey("snap:1")]: "{not json" }), "snap:1")).toBeNull();
  });

  it("treats an unknown layout name as no pin", () => {
    const raw = JSON.stringify({ layout: "dagre", positions: { "char:a": { x: 1, y: 2 } } });
    expect(loadPin(aStorage({ [pinKey("snap:1")]: raw }), "snap:1")).toBeNull();
  });

  it("drops coordinates that are not numbers rather than placing a node at NaN", () => {
    const raw = JSON.stringify({
      layout: "cose",
      positions: { "char:a": { x: 1, y: 2 }, "char:b": { x: "left", y: null } },
    });
    const pin = loadPin(aStorage({ [pinKey("snap:1")]: raw }), "snap:1");

    expect(pin?.positions).toEqual({ "char:a": { x: 1, y: 2 } });
  });

  it("treats a pin with no usable positions as no pin", () => {
    const raw = JSON.stringify({ layout: "cose", positions: {} });
    expect(loadPin(aStorage({ [pinKey("snap:1")]: raw }), "snap:1")).toBeNull();
  });

  it("survives storage that refuses to answer", () => {
    // A private window, a disabled store, a quota policy. The arrangement is not kept and
    // nothing else breaks.
    const storage = aRefusingStorage();

    expect(loadPin(storage, "snap:1")).toBeNull();
    expect(() => savePin(storage, "snap:1", aPin)).not.toThrow();
    expect(() => clearPin(storage, "snap:1")).not.toThrow();
  });
});

describe("planLayout", () => {
  it("lays everything out when nothing is pinned", () => {
    const plan = planLayout(null, ["char:a", "char:b"], "circle");

    expect(plan.preset).toEqual({});
    expect(plan.unplaced).toEqual(["char:a", "char:b"]);
    expect(plan.algorithm).toBe("circle");
    expect(plan.complete).toBe(false);
  });

  it("places every node from the pin when the pin covers them all", () => {
    const plan = planLayout(aPin, ["char:a", "char:b"]);

    expect(plan.preset).toEqual(aPin.positions);
    expect(plan.unplaced).toEqual([]);
    expect(plan.complete).toBe(true);
  });

  it("is complete for a subset, which is what makes filtering cheap", () => {
    // Once an arrangement is settled, narrowing the graph should not cost a force layout —
    // that is the better part of a second on a full novel, and it moves everyone.
    const plan = planLayout(aPin, ["char:a"]);

    expect(plan.complete).toBe(true);
    expect(plan.preset).toEqual({ "char:a": { x: 10, y: 20 } });
  });

  it("keeps the pinned nodes and lays out only the newcomers", () => {
    // A pin taken while filtered knows nothing about what was hidden. The reader's
    // arrangement survives and the new characters are fitted around it.
    const plan = planLayout(aPin, ["char:a", "char:b", "char:c"]);

    expect(Object.keys(plan.preset)).toEqual(["char:a", "char:b"]);
    expect(plan.unplaced).toEqual(["char:c"]);
    expect(plan.complete).toBe(false);
  });

  it("arranges newcomers by the layout the pin was taken under", () => {
    // Not by whatever the control happens to be showing, or the new characters would be
    // arranged on a different principle from their neighbours.
    const plan = planLayout({ ...aPin, layout: "grid" }, ["char:a", "char:c"], "concentric");

    expect(plan.algorithm).toBe("grid");
  });

  it("ignores pinned positions for nodes that are not drawn", () => {
    const plan = planLayout(aPin, ["char:b"]);

    expect(plan.preset).toEqual({ "char:b": { x: 30, y: 40 } });
    expect(plan.preset["char:a"]).toBeUndefined();
  });

  it("copes with an empty graph", () => {
    const plan = planLayout(aPin, []);

    expect(plan.preset).toEqual({});
    expect(plan.unplaced).toEqual([]);
    expect(plan.complete).toBe(true);
  });
});

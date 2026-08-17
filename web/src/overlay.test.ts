import { describe, expect, it } from "vitest";

import type { SnapshotDocument } from "./graph.js";
import {
  changeIndex,
  changeList,
  classFor,
  describeAttribution,
  pairKey,
  unchangedRelations,
  unionDocument,
  type DiffResponse,
} from "./overlay.js";

function aDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:1", name: "C" },
    works: [{ id: "work:1", title: "A Work" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "extract-v2" }],
    snapshot: { id: "snap:1", text_revision_id: "rev:1", analysis_run_id: "run:1" },
    characters: [
      { id: "char:auber", name: "Auber Vance", provenance: "observed" },
      { id: "char:idris", name: "Idris Kell", provenance: "observed" },
    ],
    relations: [
      {
        id: "rel:auber--idris",
        source: "char:auber",
        target: "char:idris",
        weight: 10,
        weight_basis: "interaction_passages",
        provenance: "observed",
      },
    ],
    ...overrides,
  };
}

function aDiff(overrides: Partial<DiffResponse> = {}): DiffResponse {
  return {
    before: "snap:1",
    after: "snap:2",
    attribution: "text",
    weights_comparable: true,
    weight_basis: "interaction_passages",
    warnings: [],
    characters: [],
    relations: [],
    ...overrides,
  };
}

describe("unionDocument", () => {
  it("keeps a character that only the earlier snapshot had", () => {
    // Drawing only the later graph would omit exactly the half of the diff a reader is
    // least able to reconstruct: the part that is gone.
    const before = aDocument({
      characters: [
        { id: "char:auber", name: "Auber Vance", provenance: "observed" },
        { id: "char:neve", name: "Neve Vance", provenance: "observed" },
      ],
    });
    const union = unionDocument(before, aDocument());

    expect(union.characters.map((c) => c.id)).toContain("char:neve");
  });

  it("keeps a relation that only the earlier snapshot had", () => {
    const before = aDocument({
      relations: [
        {
          id: "rel:auber--neve",
          source: "char:auber",
          target: "char:neve",
          weight: 4,
          weight_basis: "interaction_passages",
          provenance: "observed",
        },
      ],
    });

    const union = unionDocument(before, aDocument());
    expect(union.relations).toHaveLength(2);
  });

  it("does not duplicate what both snapshots hold", () => {
    const union = unionDocument(aDocument(), aDocument());

    expect(union.characters).toHaveLength(2);
    expect(union.relations).toHaveLength(1);
  });

  it("lets the later snapshot win where both hold the same relation", () => {
    // The overlay is a picture of where the work now stands, annotated with how it got here.
    const before = aDocument();
    const after = aDocument();
    after.relations[0].weight = 25;

    expect(unionDocument(before, after).relations[0].weight).toBe(25);
  });

  it("matches relations by endpoints rather than by identifier", () => {
    // An identifier is derived from the names the endpoints had at the time, so a merge
    // would make one edge look like two.
    const before = aDocument();
    before.relations[0].id = "rel:something--else";

    expect(unionDocument(before, aDocument()).relations).toHaveLength(1);
  });

  it("carries the later snapshot's own metadata", () => {
    const after = aDocument({
      snapshot: { id: "snap:2", text_revision_id: "rev:2", analysis_run_id: "run:1" },
    });

    expect(unionDocument(aDocument(), after).snapshot.id).toBe("snap:2");
  });
});

describe("changeIndex", () => {
  it("keys characters by identifier", () => {
    const diff = aDiff({
      characters: [{ id: "char:neve", name: "Neve", kind: "added", counterparts: [] }],
    });

    expect(changeIndex(diff).characters.get("char:neve")).toBe("added");
  });

  it("keys relations by endpoint pair, either way round", () => {
    const diff = aDiff({
      relations: [
        {
          id: "rel:x",
          source: "char:idris",
          target: "char:auber",
          kinds: ["strengthened"],
          weight_before: 4,
          weight_after: 9,
          delta: 5,
          types_before: [],
          types_after: [],
        },
      ],
    });

    const index = changeIndex(diff);
    expect(index.relations.get(pairKey("char:auber", "char:idris"))).toEqual(["strengthened"]);
  });
});

describe("classFor", () => {
  it("has nothing to say about an element that did not change", () => {
    expect(classFor([])).toBeNull();
    expect(classFor(undefined)).toBeNull();
  });

  it("prefers the change that moved the edge when several apply", () => {
    // A picture cannot say two things about one line at once, and choosing silently would
    // be worse than choosing by a stated rule. The list still says both.
    expect(classFor(["retyped", "strengthened"])).toBe("strengthened");
    expect(classFor(["strengthened", "removed"])).toBe("removed");
  });

  it("passes a single change through", () => {
    expect(classFor(["retyped"])).toBe("retyped");
  });
});

describe("changeList", () => {
  const union = aDocument({
    characters: [
      { id: "char:auber", name: "Auber Vance", provenance: "observed" },
      { id: "char:idris", name: "Idris Kell", provenance: "observed" },
      { id: "char:neve", name: "Neve Vance", provenance: "observed" },
    ],
  });

  it("names characters rather than identifiers", () => {
    const entries = changeList(
      union,
      aDiff({
        characters: [{ id: "char:neve", name: "Neve Vance", kind: "added", counterparts: [] }],
      }),
    );

    expect(entries[0].subject).toBe("Neve Vance");
    expect(entries[0].detail).toContain("appears for the first time");
  });

  it("says what a merge became", () => {
    const entries = changeList(
      union,
      aDiff({
        characters: [
          { id: "char:av", name: "Auber V.", kind: "merged", counterparts: ["char:auber"] },
        ],
      }),
    );

    expect(entries[0].detail).toBe("merged into Auber Vance");
  });

  it("says what a split came out of", () => {
    const entries = changeList(
      union,
      aDiff({
        characters: [
          { id: "char:neve", name: "Neve Vance", kind: "split", counterparts: ["char:auber"] },
        ],
      }),
    );

    expect(entries[0].detail).toBe("split out of Auber Vance");
  });

  it("gives a weight change both of its numbers", () => {
    const entries = changeList(
      union,
      aDiff({
        relations: [
          {
            id: "rel:1",
            source: "char:auber",
            target: "char:idris",
            kinds: ["weakened"],
            weight_before: 25,
            weight_after: 4,
            delta: -21,
            types_before: [],
            types_after: [],
          },
        ],
      }),
    );

    expect(entries[0].subject).toBe("Auber Vance — Idris Kell");
    expect(entries[0].detail).toBe("25 to 4");
  });

  it("reports both changes to an edge that strengthened and was retyped", () => {
    // Where the picture must choose, the list does not.
    const entries = changeList(
      union,
      aDiff({
        relations: [
          {
            id: "rel:1",
            source: "char:auber",
            target: "char:idris",
            kinds: ["strengthened", "retyped"],
            weight_before: 10,
            weight_after: 30,
            delta: 20,
            types_before: ["antagonism"],
            types_after: ["kinship"],
          },
        ],
      }),
    );

    expect(entries[0].detail).toContain("10 to 30");
    expect(entries[0].detail).toContain("antagonism to kinship");
  });

  it("calls an absent type list untyped rather than blank", () => {
    const entries = changeList(
      union,
      aDiff({
        relations: [
          {
            id: "rel:1",
            source: "char:auber",
            target: "char:idris",
            kinds: ["retyped"],
            weight_before: 10,
            weight_after: 10,
            delta: 0,
            types_before: [],
            types_after: ["kinship"],
          },
        ],
      }),
    );

    expect(entries[0].detail).toBe("untyped to kinship");
  });

  it("puts characters before relations", () => {
    const entries = changeList(
      union,
      aDiff({
        characters: [{ id: "char:neve", name: "Neve Vance", kind: "added", counterparts: [] }],
        relations: [
          {
            id: "rel:1",
            source: "char:auber",
            target: "char:neve",
            kinds: ["added"],
            weight_before: null,
            weight_after: 3,
            delta: null,
            types_before: [],
            types_after: [],
          },
        ],
      }),
    );

    expect(entries.map((entry) => entry.kind)).toEqual(["added", "added"]);
    expect(entries[0].subject).toBe("Neve Vance");
    expect(entries[1].subject).toContain("—");
  });

  it("is empty when nothing changed", () => {
    expect(changeList(union, aDiff())).toEqual([]);
  });
});

describe("describeAttribution", () => {
  it("says the text moved", () => {
    expect(describeAttribution(aDiff({ attribution: "text" }))).toContain("belong to the text");
  });

  it("says the reading moved", () => {
    expect(describeAttribution(aDiff({ attribution: "analysis" }))).toContain(
      "belong to the reading",
    );
  });

  it("refuses to lay a both-axes change at either", () => {
    expect(describeAttribution(aDiff({ attribution: "both" }))).toContain("neither");
  });

  it("speaks for the good cases too, not only the bad", () => {
    // A sentence that appears only when something is wrong trains a reader to read it as an
    // alarm rather than as the fact that decides what the screen is worth.
    expect(describeAttribution(aDiff({ attribution: "same" }))).not.toBe("");
  });
});

describe("unchangedRelations", () => {
  it("returns the edges the diff did not mention", () => {
    const union = aDocument({
      relations: [
        {
          id: "rel:a",
          source: "char:auber",
          target: "char:idris",
          weight: 10,
          weight_basis: "interaction_passages",
          provenance: "observed",
        },
        {
          id: "rel:b",
          source: "char:auber",
          target: "char:neve",
          weight: 2,
          weight_basis: "interaction_passages",
          provenance: "observed",
        },
      ],
    });

    const diff = aDiff({
      relations: [
        {
          id: "rel:b",
          source: "char:auber",
          target: "char:neve",
          kinds: ["strengthened"],
          weight_before: 1,
          weight_after: 2,
          delta: 1,
          types_before: [],
          types_after: [],
        },
      ],
    });

    expect(unchangedRelations(union, diff).map((relation) => relation.id)).toEqual(["rel:a"]);
  });
});

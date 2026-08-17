import { describe, expect, it } from "vitest";

import { NO_FILTERS, isNarrowed, optionsFor, passes, toggle, type Filters } from "./filters.js";
import {
  DEFAULT_SCALING,
  buildGraph,
  type SnapshotDocument,
  type SnapshotRelation,
} from "./graph.js";

function aRelation(overrides: Partial<SnapshotRelation> = {}): SnapshotRelation {
  return {
    id: "rel:a--b",
    source: "char:a",
    target: "char:b",
    weight: 10,
    weight_basis: "interaction_passages",
    provenance: "observed",
    ...overrides,
  };
}

function aDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "extract-v2" }],
    snapshot: { id: "snap:1", text_revision_id: "rev:1", analysis_run_id: "run:1" },
    characters: [
      { id: "char:a", name: "Ada", provenance: "observed" },
      { id: "char:b", name: "Bram", provenance: "observed" },
      { id: "char:c", name: "Cai", provenance: "observed" },
    ],
    relations: [
      aRelation({ id: "rel:a--b", weight: 40, types: ["kinship"] }),
      aRelation({
        id: "rel:a--c",
        source: "char:a",
        target: "char:c",
        weight: 2,
        types: ["rivalry"],
      }),
    ],
    ...overrides,
  };
}

function withFilters(overrides: Partial<Filters> = {}): Filters {
  return { ...NO_FILTERS, ...overrides };
}

describe("passes", () => {
  it("keeps everything by default", () => {
    expect(passes(aRelation(), NO_FILTERS)).toBe(true);
  });

  it("drops a relation below the minimum weight", () => {
    expect(passes(aRelation({ weight: 5 }), withFilters({ minimumWeight: 10 }))).toBe(false);
    expect(passes(aRelation({ weight: 10 }), withFilters({ minimumWeight: 10 }))).toBe(true);
  });

  it("keeps a relation carrying any of the wanted types", () => {
    const relation = aRelation({ types: ["kinship", "antagonism"] });

    expect(passes(relation, withFilters({ types: ["antagonism"] }))).toBe(true);
    expect(passes(relation, withFilters({ types: ["romantic"] }))).toBe(false);
  });

  it("drops an untyped relation once any type is asked for", () => {
    // Asked for the kinship edges, a reader is not asking for the edges nobody typed.
    const untyped = aRelation({ types: [] });

    expect(passes(untyped, NO_FILTERS)).toBe(true);
    expect(passes(untyped, withFilters({ types: ["kinship"] }))).toBe(false);
  });

  it("filters on provenance", () => {
    expect(passes(aRelation(), withFilters({ provenance: ["human"] }))).toBe(false);
    expect(passes(aRelation({ provenance: "human" }), withFilters({ provenance: ["human"] }))).toBe(
      true,
    );
  });

  it("requires every active filter to agree", () => {
    const relation = aRelation({ weight: 40, types: ["kinship"], provenance: "observed" });
    const filters = withFilters({ minimumWeight: 10, types: ["kinship"], provenance: ["human"] });

    expect(passes(relation, filters)).toBe(false);
  });
});

describe("optionsFor", () => {
  it("offers the relation types the snapshot actually uses, commonest first", () => {
    const document = aDocument({
      relations: [
        aRelation({ id: "r1", types: ["kinship"] }),
        aRelation({ id: "r2", types: ["kinship"] }),
        aRelation({ id: "r3", types: ["romantic"] }),
      ],
    });

    expect(optionsFor(document).types).toEqual(["kinship", "romantic"]);
  });

  it("offers no types when the run recorded none", () => {
    // The first full-novel run is exactly this: 241 relations, not one of them typed. A
    // control offering nothing suggests the graph can be narrowed in a way it cannot.
    const document = aDocument({
      relations: [aRelation({ id: "r1" }), aRelation({ id: "r2" })],
    });

    expect(optionsFor(document).types).toEqual([]);
  });

  it("offers provenance only when there is more than one to choose between", () => {
    const uniform = aDocument({
      relations: [aRelation({ id: "r1" }), aRelation({ id: "r2" })],
    });
    expect(optionsFor(uniform).provenance).toEqual([]);

    const mixed = aDocument({
      relations: [aRelation({ id: "r1" }), aRelation({ id: "r2", provenance: "human" })],
    });
    expect(optionsFor(mixed).provenance).toEqual(["observed", "human"]);
  });

  it("reports the weight range and its basis", () => {
    const options = optionsFor(aDocument());

    expect(options.minWeight).toBe(2);
    expect(options.maxWeight).toBe(40);
    expect(options.weightBasis).toBe("interaction_passages");
    expect(options.weightUsable).toBe(true);
  });

  it("refuses a weight filter when the relations disagree about the basis", () => {
    // There is no single scale for "at least 20" to be measured on. The same rule that
    // stops 2.1 printing a weight without its basis.
    const document = aDocument();
    document.relations[1].weight_basis = "words_exchanged";
    const options = optionsFor(document);

    expect(options.weightUsable).toBe(false);
    expect(options.weightBasis).toBeNull();
  });

  it("refuses a weight filter when every weight is the same", () => {
    // A floor either keeps everything or removes everything, so the control is a lie
    // about what it can do.
    const document = aDocument({
      relations: [aRelation({ id: "r1", weight: 7 }), aRelation({ id: "r2", weight: 7 })],
    });

    expect(optionsFor(document).weightUsable).toBe(false);
  });

  it("copes with a snapshot holding no relations", () => {
    const options = optionsFor(aDocument({ relations: [] }));

    expect(options.types).toEqual([]);
    expect(options.maxWeight).toBe(0);
    expect(options.weightUsable).toBe(false);
  });
});

describe("buildGraph with filters", () => {
  it("draws every relation when nothing is narrowed", () => {
    const { relationsShown, relationsTotal, charactersHidden } = buildGraph(aDocument());

    expect(relationsShown).toBe(2);
    expect(relationsTotal).toBe(2);
    expect(charactersHidden).toBe(0);
  });

  it("drops the relations the filter excludes and says how many remain", () => {
    const built = buildGraph(aDocument(), withFilters({ minimumWeight: 10 }));

    expect(built.relationsShown).toBe(1);
    expect(built.relationsTotal).toBe(2);
  });

  it("hides a character the filter emptied, and counts it", () => {
    // Cai's only relation is the light one. Drawing them as a dimmed dot would hide the
    // structure the filter was applied to reveal.
    const built = buildGraph(aDocument(), withFilters({ minimumWeight: 10 }));
    const ids = built.elements.map((element) => element.data.id);

    expect(ids).not.toContain("char:c");
    expect(built.charactersHidden).toBe(1);
  });

  it("still draws a character the snapshot itself left with no relations", () => {
    // Having none is a fact about the snapshot rather than a consequence of the filter, so
    // it is not the filter's business to hide it.
    const document = aDocument({
      characters: [
        { id: "char:a", name: "Ada", provenance: "observed" },
        { id: "char:b", name: "Bram", provenance: "observed" },
        { id: "char:lonely", name: "Nobody", provenance: "observed" },
      ],
      relations: [aRelation({ id: "rel:a--b", weight: 40 })],
    });

    const built = buildGraph(document, withFilters({ minimumWeight: 10 }));
    const lonely = built.elements.find((element) => element.data.id === "char:lonely");

    expect(lonely).toBeDefined();
    expect(lonely?.classes).toBe("isolated");
    expect(built.charactersHidden).toBe(0);
  });

  it("rescales what is left rather than keeping the old maximum", () => {
    // The heaviest surviving edge should read as the heaviest, or a filtered view renders
    // every remaining edge as a hairline against a maximum no longer on screen.
    const built = buildGraph(aDocument(), withFilters({ minimumWeight: 30 }));
    const edge = built.elements.find((element) => element.data.id === "rel:a--b");

    expect(built.maxWeight).toBe(40);
    expect(edge?.data.width).toBe(14);
  });

  it("can filter everything away without breaking", () => {
    const built = buildGraph(aDocument(), withFilters({ minimumWeight: 1000 }));

    expect(built.relationsShown).toBe(0);
    expect(built.charactersHidden).toBe(3);
    expect(built.elements).toEqual([]);
  });
});

describe("isNarrowed", () => {
  it("is false for the default filters", () => {
    expect(isNarrowed(NO_FILTERS)).toBe(false);
  });

  it("is true once anything is set", () => {
    expect(isNarrowed(withFilters({ minimumWeight: 1 }))).toBe(true);
    expect(isNarrowed(withFilters({ types: ["kinship"] }))).toBe(true);
    expect(isNarrowed(withFilters({ provenance: ["human"] }))).toBe(true);
  });
});

describe("toggle", () => {
  it("adds a value that is not there", () => {
    expect(toggle(["a"], "b")).toEqual(["a", "b"]);
  });

  it("removes a value that is", () => {
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
  });

  it("does not mutate the list it was given", () => {
    const original = ["a"];
    toggle(original, "b");
    expect(original).toEqual(["a"]);
  });
});

describe("absolute and relative scaling", () => {
  /** Three edges, so filtering the heaviest away still leaves something to compare. */
  function aSpread() {
    return aDocument({
      characters: [
        { id: "char:a", name: "Ada", provenance: "observed" },
        { id: "char:b", name: "Bram", provenance: "observed" },
        { id: "char:c", name: "Cai", provenance: "observed" },
        { id: "char:d", name: "Dov", provenance: "observed" },
      ],
      relations: [
        // Typed so the heaviest can be filtered away: the weight filter is a floor, so it
        // can only ever remove the light end.
        aRelation({
          id: "rel:heavy",
          source: "char:a",
          target: "char:b",
          weight: 100,
          types: ["duel"],
        }),
        aRelation({
          id: "rel:middle",
          source: "char:a",
          target: "char:c",
          weight: 25,
          types: ["kinship"],
        }),
        aRelation({
          id: "rel:light",
          source: "char:a",
          target: "char:d",
          weight: 4,
          types: ["kinship"],
        }),
      ],
    });
  }

  function widthOf(built: ReturnType<typeof buildGraph>, id: string) {
    return built.elements.find((element) => element.data.id === id)?.data.width;
  }

  it("keeps a surviving edge the same width when the heaviest is filtered away", () => {
    // The property the default exists for. Nothing about the work changed, so nothing about
    // the picture should.
    const whole = buildGraph(aSpread(), NO_FILTERS, "absolute");
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "absolute");

    expect(widthOf(narrowed, "rel:middle")).toBe(widthOf(whole, "rel:middle"));
  });

  it("thickens the survivors under relative scaling, which is the failure", () => {
    // Documented as a test rather than as a claim: the relative view really does say the
    // survivors are more central than it said a moment ago.
    const whole = buildGraph(aSpread(), NO_FILTERS, "relative");
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "relative");

    expect(widthOf(narrowed, "rel:middle")).toBeGreaterThan(widthOf(whole, "rel:middle") as number);
  });

  it("measures against the whole snapshot when absolute", () => {
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "absolute");

    expect(narrowed.maxWeight).toBe(100);
    expect(narrowed.scaling).toBe("absolute");
  });

  it("measures against what is drawn when relative", () => {
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "relative");

    expect(narrowed.maxWeight).toBe(25);
    expect(narrowed.scaling).toBe("relative");
  });

  it("gives the heaviest drawn edge the full width under relative scaling", () => {
    // Which is the reason to offer it: a narrowed view uses its whole range.
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "relative");
    expect(widthOf(narrowed, "rel:middle")).toBe(14);
  });

  it("holds node sizes still as well, so the two encodings agree", () => {
    // graph.ts requires the two to read consistently, so the same reference governs both.
    // Cai is the test: the filter does not touch Cai's one relation, so Cai's degree is
    // unchanged and Cai's dot should be too. Ada's dot legitimately shrinks, because Ada
    // really does have fewer relations in this view — that is the data, not the scale.
    const size = (built: ReturnType<typeof buildGraph>, id: string) =>
      built.elements.find((element) => element.data.id === id)?.data.size;

    const whole = buildGraph(aSpread(), NO_FILTERS, "absolute");
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "absolute");

    expect(size(narrowed, "char:c")).toBe(size(whole, "char:c"));
    expect(narrowed.maxDegree).toBe(whole.maxDegree);
  });

  it("resizes an untouched character under relative scaling", () => {
    // The same failure as the edges, on the other encoding: Cai gained nothing and lost
    // nothing, and the picture says Cai is more connected than it did a moment ago.
    const size = (built: ReturnType<typeof buildGraph>, id: string) =>
      built.elements.find((element) => element.data.id === id)?.data.size;

    const whole = buildGraph(aSpread(), NO_FILTERS, "relative");
    const narrowed = buildGraph(aSpread(), withFilters({ types: ["kinship"] }), "relative");

    expect(size(narrowed, "char:c")).toBeGreaterThan(size(whole, "char:c") as number);
  });

  it("is unchanged by the choice when nothing is filtered", () => {
    const absolute = buildGraph(aSpread(), NO_FILTERS, "absolute");
    const relative = buildGraph(aSpread(), NO_FILTERS, "relative");

    expect(absolute.maxWeight).toBe(relative.maxWeight);
    expect(widthOf(absolute, "rel:light")).toBe(widthOf(relative, "rel:light"));
  });

  it("defaults to absolute", () => {
    expect(buildGraph(aSpread()).scaling).toBe(DEFAULT_SCALING);
    expect(DEFAULT_SCALING).toBe("absolute");
  });
});

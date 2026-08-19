import { describe, expect, it } from "vitest";

import {
  UNCERTAIN_BELOW,
  confidenceOf,
  describe as describeConfidence,
  isRecorded,
  isUncertain,
  legend,
  summarise,
  uncertainOf,
} from "./confidence.js";
import type { SnapshotDocument, SnapshotRelation } from "./graph.js";

function aRelation(overrides: Partial<SnapshotRelation> = {}): SnapshotRelation {
  return {
    id: "rel:a--b",
    source: "char:a",
    target: "char:b",
    weight: 3,
    weight_basis: "interaction_passages",
    provenance: "observed",
    ...overrides,
  } as SnapshotRelation;
}

function aDocument(relations: SnapshotRelation[], characters = []): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "p" }],
    snapshot: {
      id: "snap:1",
      work_id: "work:1",
      text_revision_id: "rev:1",
      analysis_run_id: "run:1",
    },
    characters,
    relations,
  } as unknown as SnapshotDocument;
}

describe("reading a recorded confidence", () => {
  it("takes a value on the scale the schema declares", () => {
    expect(confidenceOf({ confidence: 0.9 })).toBe(0.9);
    expect(confidenceOf({ confidence: 0 })).toBe(0);
    expect(confidenceOf({ confidence: 1 })).toBe(1);
  });

  it("treats an absent one as unsaid rather than as zero", () => {
    // The whole of this module's first rule. Zero is a claim; silence is not.
    expect(confidenceOf({})).toBeNull();
    expect(confidenceOf(undefined)).toBeNull();
    expect(confidenceOf(null)).toBeNull();
  });

  it("treats a value off the scale as unsaid rather than clamping it", () => {
    // Clamping would turn a malformed document into a claim nobody made.
    expect(confidenceOf({ confidence: 1.4 })).toBeNull();
    expect(confidenceOf({ confidence: -0.2 })).toBeNull();
    expect(confidenceOf({ confidence: Number.NaN })).toBeNull();
  });
});

describe("which elements are marked", () => {
  it("marks one the reading was less than half sure of", () => {
    expect(isUncertain({ confidence: 0.2 })).toBe(true);
    expect(isUncertain({ confidence: 0.49 })).toBe(true);
  });

  it("leaves the midpoint itself unmarked", () => {
    // Below, not at: at 0.50 a reading is exactly as sure as not, and the mark is for the
    // edges it was more unsure than sure of.
    expect(isUncertain({ confidence: UNCERTAIN_BELOW })).toBe(false);
    expect(isUncertain({ confidence: 0.9 })).toBe(false);
  });

  it("never marks an element the reading said nothing about", () => {
    // An unqualified graph must look exactly as it looks today. Marking every edge a run
    // failed to qualify would be the absent-is-not-low mistake made across a whole picture.
    expect(isUncertain({})).toBe(false);
    expect(isUncertain(undefined)).toBe(false);
  });
});

describe("summarising a snapshot", () => {
  it("counts what was recorded and what was low", () => {
    const summary = summarise(
      aDocument([
        aRelation({ id: "rel:1", confidence: 0.2 }),
        aRelation({ id: "rel:2", confidence: 0.8 }),
        aRelation({ id: "rel:3" }),
      ]),
    );

    expect(summary.relations).toBe(3);
    expect(summary.recorded).toBe(2);
    expect(summary.uncertain).toBe(1);
    expect(summary.lowest).toBe(0.2);
  });

  it("reports nothing recorded rather than everything confident", () => {
    const summary = summarise(aDocument([aRelation(), aRelation({ id: "rel:2" })]));

    expect(summary.recorded).toBe(0);
    expect(summary.lowest).toBeNull();
    expect(isRecorded(summary)).toBe(false);
  });

  it("survives a snapshot that has not loaded", () => {
    expect(summarise(null).relations).toBe(0);
    expect(isRecorded(summarise(null))).toBe(false);
  });
});

describe("what the sidebar says", () => {
  it("says so plainly when a reading records none", () => {
    // Every graph Dramatis has produced so far is this case, so it is the sentence that
    // matters most.
    expect(describeConfidence(summarise(aDocument([aRelation()])))).toBe(
      "not recorded by this reading",
    );
  });

  it("names how many were low when some were", () => {
    const summary = summarise(
      aDocument([aRelation({ confidence: 0.2 }), aRelation({ id: "rel:2", confidence: 0.9 })]),
    );

    expect(describeConfidence(summary)).toBe("2 relation(s), 1 below 0.50");
  });

  it("says none were low rather than leaving the reader to infer it", () => {
    const summary = summarise(aDocument([aRelation({ confidence: 0.9 })]));

    expect(describeConfidence(summary)).toBe("1 relation(s), none below 0.50");
  });

  it("distinguishes a partly qualified reading from a wholly qualified one", () => {
    const summary = summarise(
      aDocument([aRelation({ confidence: 0.9 }), aRelation({ id: "rel:2" })]),
    );

    expect(describeConfidence(summary)).toBe("1 of 2 relation(s), none below 0.50");
  });
});

describe("the legend", () => {
  it("is withheld where nothing on screen carries the mark", () => {
    // A legend for an encoding nothing uses is the control 2.5 refused: it looks like
    // information and is not.
    expect(legend(summarise(aDocument([aRelation()])))).toBeNull();
  });

  it("explains the mark and the silence where there is something to explain", () => {
    const text = legend(summarise(aDocument([aRelation({ confidence: 0.2 })])));

    expect(text).toContain("Dotted");
    expect(text).toContain("0.50");
    expect(text).toContain("solid");
  });
});

describe("listing what a reading was unsure of", () => {
  it("finds the elements that carry the mark", () => {
    const document = aDocument([
      aRelation({ id: "rel:1", confidence: 0.1 }),
      aRelation({ id: "rel:2", confidence: 0.99 }),
      aRelation({ id: "rel:3" }),
    ]);

    expect(uncertainOf(document).relations.map((relation) => relation.id)).toEqual(["rel:1"]);
  });

  it("survives a snapshot that has not loaded", () => {
    expect(uncertainOf(null)).toEqual({ characters: [], relations: [] });
  });
});

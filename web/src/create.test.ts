import { describe, expect, it } from "vitest";

import {
  describePlan,
  excludedCount,
  initialChoices,
  isReady,
  plansFor,
  undecided,
  type Choice,
  type ProposedDocument,
  type ProposedStructure,
} from "./create.js";

function aProposal(over: Partial<ProposedDocument> = {}): ProposedDocument {
  return {
    path: "novel.txt",
    characters: 1000,
    role: { value: "unknown", basis: "needs the text read", settled: false },
    addressing: { value: "section", basis: "D27", settled: true },
    revision_of: { value: null, basis: "no earlier document", settled: false },
    regions: [
      {
        label: "whole document",
        role: { value: "unknown", basis: "needs the text read", settled: false },
        starts_at: 0,
        ends_at: 1000,
        begins_with: "",
        ends_with: "",
      },
    ],
    ...over,
  };
}

function aStructure(documents: ProposedDocument[]): ProposedStructure {
  return { root: "/corpus", documents, skipped: [], notes: [] };
}

describe("where the flow starts", () => {
  it("offers no role for a document nobody has classified", () => {
    // A pre-filled guess is a guess somebody accepts without reading.
    const choices = initialChoices(aStructure([aProposal()]));

    expect(choices["novel.txt"].role).toBeNull();
  });

  it("offers back a role somebody already confirmed", () => {
    const settled = aProposal({
      role: { value: "reference", basis: "confirmed by you", settled: true },
    });

    expect(initialChoices(aStructure([settled]))["novel.txt"].role).toBe("reference");
  });

  it("does not offer back an unsettled model proposal as a decision", () => {
    // propose_with_model marks a role unsettled until a person confirms it; the flow must
    // not present it as already decided.
    const proposed = aProposal({
      role: { value: "narrative", basis: "read by a model", settled: false },
    });

    expect(initialChoices(aStructure([proposed]))["novel.txt"].role).toBeNull();
  });

  it("keeps a boundary that was already confirmed", () => {
    const excluded = aProposal({
      regions: [
        {
          label: "before the narrative",
          role: { value: "excluded", basis: "confirmed", settled: true },
          starts_at: 0,
          ends_at: null,
          begins_with: "",
          ends_with: "",
        },
        {
          label: "narrative",
          role: { value: "narrative", basis: "confirmed", settled: true },
          starts_at: 0,
          ends_at: null,
          begins_with: "It is a truth universally acknowledged",
          ends_with: "",
        },
      ],
    });

    expect(initialChoices(aStructure([excluded]))["novel.txt"].excludeBefore).toBe(
      "It is a truth universally acknowledged",
    );
  });

  it("finds no boundary where nothing is excluded", () => {
    expect(initialChoices(aStructure([aProposal()]))["novel.txt"].excludeBefore).toBe("");
  });
});

describe("when the flow may finish", () => {
  const structure = aStructure([aProposal(), aProposal({ path: "notes.md" })]);

  it("is not ready while any document lacks a role", () => {
    const choices: Record<string, Choice> = {
      "novel.txt": { role: "narrative", excludeBefore: "" },
      "notes.md": { role: null, excludeBefore: "" },
    };

    expect(isReady(structure, choices)).toBe(false);
    expect(undecided(structure, choices)).toEqual(["notes.md"]);
  });

  it("is ready when every document has one", () => {
    const choices: Record<string, Choice> = {
      "novel.txt": { role: "narrative", excludeBefore: "" },
      "notes.md": { role: "reference", excludeBefore: "" },
    };

    expect(isReady(structure, choices)).toBe(true);
  });

  it("is not ready when there is nothing readable to ingest", () => {
    // An empty folder must not offer a create button that would write nothing.
    expect(isReady(aStructure([]), {})).toBe(false);
  });
});

describe("the map it saves", () => {
  it("is the shape the store and the ingest already speak", () => {
    // The load-bearing test: ingest.kept_text reads regions[].role.value and begins_with,
    // and ingest reads role.value for the document. A browser-only shape would save
    // something nothing downstream acts on.
    const structure = aStructure([aProposal()]);
    const plans = plansFor(structure, {
      "novel.txt": { role: "narrative", excludeBefore: "It is a truth" },
    }) as Record<string, any>;
    const plan = plans["novel.txt"];

    expect(plan.role.value).toBe("narrative");
    expect(plan.role.settled).toBe(true);
    expect(plan.regions[0].role.value).toBe("excluded");
    expect(plan.regions[1].begins_with).toBe("It is a truth");
  });

  it("keeps a document whole when nothing is excluded", () => {
    const plans = plansFor(aStructure([aProposal()]), {
      "novel.txt": { role: "narrative", excludeBefore: "" },
    }) as Record<string, any>;

    expect(plans["novel.txt"].regions).toHaveLength(1);
    expect(plans["novel.txt"].regions[0].label).toBe("whole document");
    expect(plans["novel.txt"].regions[0].role.value).toBe("narrative");
  });

  it("ignores surrounding whitespace on a boundary", () => {
    const plans = plansFor(aStructure([aProposal()]), {
      "novel.txt": { role: "narrative", excludeBefore: "   \n  " },
    }) as Record<string, any>;

    expect(plans["novel.txt"].regions).toHaveLength(1);
  });

  it("leaves out a document with no role rather than inventing one", () => {
    const plans = plansFor(aStructure([aProposal(), aProposal({ path: "notes.md" })]), {
      "novel.txt": { role: "narrative", excludeBefore: "" },
      "notes.md": { role: null, excludeBefore: "" },
    });

    expect(Object.keys(plans)).toEqual(["novel.txt"]);
  });

  it("carries the addressing and revision the proposal evidenced", () => {
    // Those were read off the corpus; the browser confirms a role, it does not re-derive them.
    const plans = plansFor(aStructure([aProposal()]), {
      "novel.txt": { role: "narrative", excludeBefore: "" },
    }) as Record<string, any>;

    expect(plans["novel.txt"].addressing.value).toBe("section");
  });

  it("survives a round trip through JSON", () => {
    const plans = plansFor(aStructure([aProposal()]), {
      "novel.txt": { role: "reference", excludeBefore: "start here" },
    });

    expect(JSON.parse(JSON.stringify(plans))).toEqual(plans);
  });
});

describe("what it says before committing", () => {
  it("names the documents still needing a role", () => {
    const structure = aStructure([aProposal(), aProposal({ path: "notes.md" })]);
    const sentence = describePlan(structure, {
      "novel.txt": { role: "narrative", excludeBefore: "" },
      "notes.md": { role: null, excludeBefore: "" },
    });

    expect(sentence).toContain("notes.md");
    expect(sentence).toContain("still needs a role");
  });

  it("summarises what creating will do", () => {
    const structure = aStructure([aProposal(), aProposal({ path: "notes.md" })]);
    const sentence = describePlan(structure, {
      "novel.txt": { role: "narrative", excludeBefore: "It is a truth" },
      "notes.md": { role: "reference", excludeBefore: "" },
    });

    expect(sentence).toContain("2 documents");
    expect(sentence).toContain("1 as reference material");
    expect(sentence).toContain("front matter dropped from 1");
  });

  it("says so when there is nothing to ingest", () => {
    expect(describePlan(aStructure([]), {})).toContain("nothing readable");
  });

  it("counts the documents losing front matter", () => {
    expect(
      excludedCount({
        a: { role: "narrative", excludeBefore: "x" },
        b: { role: "narrative", excludeBefore: "" },
      }),
    ).toBe(1);
  });
});

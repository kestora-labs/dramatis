import { describe, expect, it } from "vitest";

import {
  decimal,
  describeCharacter,
  describeRelation,
  describeSelection,
  unitInterval,
  valence,
  type Detail,
  type DetailField,
} from "./detail.js";
import type { SnapshotDocument } from "./graph.js";

/** A document in the shape a real model run produces: the required fields and no more. */
function aSparseDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "extract-v2" }],
    snapshot: { id: "snap:1", text_revision_id: "rev:1", analysis_run_id: "run:1" },
    characters: [
      { id: "char:a", name: "Ada", provenance: "observed" },
      { id: "char:b", name: "Bram", provenance: "observed" },
    ],
    relations: [
      {
        id: "rel:a--b",
        source: "char:a",
        target: "char:b",
        weight: 12,
        weight_basis: "interaction_passages",
        provenance: "observed",
      },
    ],
    ...overrides,
  };
}

/** A document in the shape fixture A has: every optional qualifier populated. */
function aRichDocument(): SnapshotDocument {
  return aSparseDocument({
    characters: [
      {
        id: "char:a",
        name: "Elizabeth Bennet",
        aliases: ["Lizzy", "Eliza", "Miss Elizabeth Bennet"],
        kind: "person",
        salience: 1,
        confidence: 0.9,
        provenance: "human",
        review_status: "accepted",
        notes: "The novel's centre of consciousness.",
      },
      { id: "char:b", name: "Fitzwilliam Darcy", kind: "person", provenance: "human" },
    ],
    relations: [
      {
        id: "rel:a--b",
        source: "char:a",
        target: "char:b",
        weight: 100,
        weight_basis: "hand_assigned_prominence",
        directed: false,
        types: ["romantic", "antagonism"],
        valence: 0.4,
        confidence: 1,
        provenance: "human",
        review_status: "accepted",
        notes: "Opens in mutual slight and closes in marriage.",
      },
    ],
  });
}

function labels(fields: DetailField[]): string[] {
  return fields.map((field) => field.label);
}

function valueOf(detail: Detail, label: string): string | undefined {
  return detail.fields.find((field) => field.label === label)?.value;
}

describe("number formatting", () => {
  it("leaves a count as a count", () => {
    // A weight of 100 interaction passages is a tally. Rendering it as 100.00 would dress
    // it up as a measurement.
    expect(decimal(100)).toBe("100");
    expect(decimal(0)).toBe("0");
  });

  it("gives a fractional weight two places", () => {
    expect(decimal(2.5)).toBe("2.50");
    expect(decimal(1.239)).toBe("1.24");
  });

  it("puts unit-interval values on a consistent scale", () => {
    // 1 beside 0.85 reads as a different kind of quantity from its neighbour.
    expect(unitInterval(1)).toBe("1.00");
    expect(unitInterval(0.85)).toBe("0.85");
    expect(unitInterval(0)).toBe("0.00");
  });

  it("keeps the sign on valence, where the sign is the meaning", () => {
    expect(valence(0.4)).toBe("+0.40");
    expect(valence(-0.6)).toBe("-0.60");
    expect(valence(0)).toBe("0.00");
  });

  it("survives nonsense input", () => {
    expect(decimal(NaN)).toBe("—");
    expect(unitInterval(Infinity)).toBe("—");
    expect(valence(NaN)).toBe("—");
  });
});

describe("describeCharacter", () => {
  it("titles the panel with the name and lists the aliases", () => {
    const document = aRichDocument();
    const detail = describeCharacter(document, document.characters[0]);

    expect(detail.title).toBe("Elizabeth Bennet");
    expect(detail.aliases).toEqual(["Lizzy", "Eliza", "Miss Elizabeth Bennet"]);
  });

  it("reports the qualifiers the document carries", () => {
    const document = aRichDocument();
    const detail = describeCharacter(document, document.characters[0]);

    expect(valueOf(detail, "Kind")).toBe("person");
    expect(valueOf(detail, "Salience")).toBe("1.00");
    expect(valueOf(detail, "Confidence")).toBe("0.90");
    expect(valueOf(detail, "Provenance")).toBe("human");
    expect(detail.notes).toBe("The novel's centre of consciousness.");
  });

  it("leaves review status to the review overlay, even when the document declares one", () => {
    // From 5.1 a decision is recorded beside the immutable snapshot, so the status baked
    // into the document is stale the moment somebody rules on the claim. One place shows
    // where review stands; a field here would be a second place, disagreeing.
    const document = aRichDocument();

    expect(labels(describeCharacter(document, document.characters[0]).fields)).not.toContain(
      "Review",
    );
    expect(labels(describeRelation(document, document.relations[0]).fields)).not.toContain(
      "Review",
    );
  });

  it("omits a qualifier the document does not carry rather than showing it empty", () => {
    // A snapshot recording no confidence is not a snapshot with low confidence. A blank
    // row would invent a claim the run never made.
    const document = aSparseDocument();
    const detail = describeCharacter(document, document.characters[0]);

    expect(labels(detail.fields)).not.toContain("Salience");
    expect(labels(detail.fields)).not.toContain("Confidence");
    expect(detail.notes).toBeUndefined();
    expect(detail.aliases).toEqual([]);
  });

  it("reports the degree, because that is what node size encodes", () => {
    const document = aSparseDocument();
    const detail = describeCharacter(document, document.characters[0]);

    expect(valueOf(detail, "Relations")).toBe("1");
  });

  it("shows a degree of zero rather than omitting it", () => {
    // Zero relations is a claim the graph makes visibly — the node is drawn isolated — so
    // unlike an absent confidence there is something to report.
    const document = aSparseDocument({ relations: [] });
    const detail = describeCharacter(document, document.characters[0]);

    expect(valueOf(detail, "Relations")).toBe("0");
  });

  it("does not let an undrawable relation inflate the degree", () => {
    const document = aSparseDocument();
    document.relations.push({
      id: "rel:ghost",
      source: "char:a",
      target: "char:missing",
      weight: 5,
      weight_basis: "interaction_passages",
      provenance: "observed",
    });

    const detail = describeCharacter(document, document.characters[0]);
    expect(valueOf(detail, "Relations")).toBe("1");
  });

  it("sets identifiers and stored vocabulary in code, and prose not", () => {
    const document = aRichDocument();
    const detail = describeCharacter(document, document.characters[0]);
    const coded = detail.fields.filter((field) => field.code).map((field) => field.label);

    expect(coded).toContain("Kind");
    expect(coded).toContain("Provenance");
    expect(coded).toContain("Id");
    expect(coded).not.toContain("Relations");
  });
});

describe("describeRelation", () => {
  it("names both endpoints instead of their ids", () => {
    const document = aRichDocument();
    const detail = describeRelation(document, document.relations[0]);

    expect(detail.title).toBe("Elizabeth Bennet — Fitzwilliam Darcy");
  });

  it("shows direction in the join for a directed relation", () => {
    const document = aRichDocument();
    document.relations[0].directed = true;
    const detail = describeRelation(document, document.relations[0]);

    expect(detail.title).toBe("Elizabeth Bennet → Fitzwilliam Darcy");
  });

  it("never shows a weight without its basis", () => {
    // Weights are comparable only within a shared basis, so a bare number has no unit.
    const document = aRichDocument();
    const detail = describeRelation(document, document.relations[0]);

    expect(valueOf(detail, "Weight")).toBe("100 hand_assigned_prominence");
    expect(labels(detail.fields)).not.toContain("Basis");
  });

  it("lists the relation types as stored", () => {
    const document = aRichDocument();
    const detail = describeRelation(document, document.relations[0]);

    expect(detail.types).toEqual(["romantic", "antagonism"]);
  });

  it("reports the remaining qualifiers", () => {
    const document = aRichDocument();
    const detail = describeRelation(document, document.relations[0]);

    expect(valueOf(detail, "Valence")).toBe("+0.40");
    expect(valueOf(detail, "Confidence")).toBe("1.00");
    expect(valueOf(detail, "Provenance")).toBe("human");
    expect(detail.notes).toBe("Opens in mutual slight and closes in marriage.");
  });

  it("omits types and qualifiers a real run does not produce", () => {
    const document = aSparseDocument();
    const detail = describeRelation(document, document.relations[0]);

    expect(detail.types).toEqual([]);
    expect(labels(detail.fields)).not.toContain("Valence");
    expect(labels(detail.fields)).not.toContain("Confidence");
    expect(valueOf(detail, "Weight")).toBe("12 interaction_passages");
  });

  it("carries the supporting passages, in the order they occur in the work", () => {
    const document = aSparseDocument();
    document.relations[0].evidence = [
      { selector: { exact: "two" }, locator: { path: [{ type: "section", index: 2 }] } },
      { selector: { exact: "one" }, locator: { path: [{ type: "section", index: 1 }] } },
    ];

    const detail = describeRelation(document, document.relations[0]);
    expect(detail.evidence.map((piece) => piece.quotation)).toEqual(["one", "two"]);
    expect(detail.evidence.map((piece) => piece.locator)).toEqual(["section 1", "section 2"]);
  });

  it("does not also state the evidence count as a field", () => {
    // The list is printed directly below and carries its own count; a field would be the
    // same fact twice.
    const document = aSparseDocument();
    document.relations[0].evidence = [
      { selector: { exact: "one" }, locator: { path: [{ type: "section", index: 1 }] } },
    ];

    expect(labels(describeRelation(document, document.relations[0]).fields)).not.toContain(
      "Evidence",
    );
  });

  it("falls back to the raw id for an endpoint the document does not contain", () => {
    // buildGraph drops such an edge before it can be selected, so this is unreachable
    // through the UI. It stays total rather than throwing if it ever is reached.
    const document = aSparseDocument();
    document.relations[0].target = "char:missing";
    const detail = describeRelation(document, document.relations[0]);

    expect(detail.title).toBe("Ada — char:missing");
  });
});

describe("describeSelection", () => {
  it("describes a selected character", () => {
    const document = aRichDocument();
    const detail = describeSelection(document, { kind: "character", id: "char:a" });

    expect(detail?.kind).toBe("character");
    expect(detail?.title).toBe("Elizabeth Bennet");
  });

  it("describes a selected relation", () => {
    const document = aRichDocument();
    const detail = describeSelection(document, { kind: "relation", id: "rel:a--b" });

    expect(detail?.kind).toBe("relation");
  });

  it("returns nothing when nothing is selected", () => {
    expect(describeSelection(aRichDocument(), null)).toBeNull();
  });

  it("returns nothing for a selection this document does not contain", () => {
    // Normal, not exceptional: switching snapshots leaves the old selection behind.
    expect(describeSelection(aRichDocument(), { kind: "character", id: "char:gone" })).toBeNull();
    expect(describeSelection(aRichDocument(), { kind: "relation", id: "rel:gone" })).toBeNull();
  });

  it("resolves by kind, not by guessing from the id", () => {
    // Ids are unique within a kind and not across one, so the same string may name both a
    // character and a relation. The kind decides which list is searched.
    const document = aSparseDocument({
      characters: [
        { id: "shared", name: "Ada", provenance: "observed" },
        { id: "char:b", name: "Bram", provenance: "observed" },
      ],
      relations: [
        {
          id: "shared",
          source: "shared",
          target: "char:b",
          weight: 3,
          weight_basis: "interaction_passages",
          provenance: "observed",
        },
      ],
    });

    expect(describeSelection(document, { kind: "character", id: "shared" })?.title).toBe("Ada");
    expect(describeSelection(document, { kind: "relation", id: "shared" })?.title).toBe(
      "Ada — Bram",
    );
  });
});

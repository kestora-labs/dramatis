import { describe, expect, it } from "vitest";

import {
  appliedIn,
  canSubmit,
  characterKinds,
  conflictsFor,
  correctionBody,
  correctionsFor,
  correctionsUrl,
  currentText,
  describe as describeValue,
  fieldsFor,
  parseValue,
  sameValue,
  withCorrection,
  type ConflictEntry,
  type CorrectionEntry,
  type CorrectionsPayload,
} from "./correction.js";
import type { SnapshotDocument } from "./graph.js";

function aDocument(): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    text_revisions: [{ id: "rev:1", work_id: "work:1", sha256: "0".repeat(64) }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "p" }],
    snapshot: {
      id: "snap:1",
      work_id: "work:1",
      text_revision_id: "rev:1",
      analysis_run_id: "run:1",
    },
    characters: [
      { id: "char:a", name: "Ada", kind: "person", aliases: ["Miss A"], provenance: "observed" },
    ],
    relations: [
      {
        id: "rel:a--b",
        source: "char:a",
        target: "char:b",
        weight: 3,
        weight_basis: "interaction_passages",
        provenance: "observed",
      },
    ],
  } as SnapshotDocument;
}

function aCorrection(overrides: Partial<CorrectionEntry> = {}): CorrectionEntry {
  return {
    kind: "character",
    id: "char:a",
    field: "name",
    value: "Ada Mbeki",
    was: "Ada",
    note: null,
    corrected_at: "2026-01-01T00:00:00+00:00",
    corrected_in: "snap:1",
    ...overrides,
  };
}

function aConflict(overrides: Partial<ConflictEntry> = {}): ConflictEntry {
  return {
    kind: "character",
    id: "char:a",
    field: "kind",
    proposed: "collective",
    held: "entity",
    noticed_at: "2026-02-02T00:00:00+00:00",
    noticed_in: "snap:2",
    ...overrides,
  };
}

function aPayload(overrides: Partial<CorrectionsPayload> = {}): CorrectionsPayload {
  return {
    snapshot_id: "snap:1",
    work_id: "work:1",
    corrections: [],
    conflicts: [],
    correctable: {
      character: ["name", "kind", "aliases", "notes"],
      relation: ["types", "valence", "directed", "notes"],
    },
    character_kinds: ["person", "collective", "entity", "unknown"],
    ...overrides,
  };
}

describe("what the server says may be corrected", () => {
  it("is taken from the payload rather than written down again here", () => {
    // A fourth copy of the vocabulary is a fourth place for it to drift, and the drift would
    // show as a form offering a field the API refuses.
    expect(fieldsFor(aPayload(), "character")).toEqual(["name", "kind", "aliases", "notes"]);
    expect(fieldsFor(aPayload(), "relation")).toEqual(["types", "valence", "directed", "notes"]);
    expect(characterKinds(aPayload())).toContain("collective");
  });

  it("is empty before the payload has loaded, rather than guessed", () => {
    expect(fieldsFor(null, "character")).toEqual([]);
    expect(characterKinds(null)).toEqual([]);
  });

  it("escapes the snapshot in the URL it asks for", () => {
    expect(correctionsUrl("snap:a b")).toBe("/api/snapshots/snap%3Aa%20b/corrections");
  });
});

describe("what stands against the selection", () => {
  it("finds only this subject's corrections", () => {
    const payload = aPayload({
      corrections: [aCorrection(), aCorrection({ id: "char:b", value: "Bram Reiner" })],
    });

    const found = correctionsFor(payload, { kind: "character", id: "char:a" });
    expect(found).toHaveLength(1);
    expect(found[0].value).toBe("Ada Mbeki");
  });

  it("does not confuse a character with a relation of the same id", () => {
    const payload = aPayload({ corrections: [aCorrection({ kind: "relation", id: "x" })] });

    expect(correctionsFor(payload, { kind: "character", id: "x" })).toEqual([]);
    expect(correctionsFor(payload, { kind: "relation", id: "x" })).toHaveLength(1);
  });

  it("finds the disagreements this reading raised", () => {
    const payload = aPayload({ conflicts: [aConflict()] });

    expect(conflictsFor(payload, { kind: "character", id: "char:a" })).toHaveLength(1);
    expect(conflictsFor(payload, { kind: "character", id: "char:z" })).toEqual([]);
  });

  it("is empty with nothing selected", () => {
    expect(correctionsFor(aPayload({ corrections: [aCorrection()] }), null)).toEqual([]);
    expect(conflictsFor(aPayload({ conflicts: [aConflict()] }), null)).toEqual([]);
  });
});

describe("folding in a fresh correction", () => {
  it("replaces the one for that field rather than appending beside it", () => {
    const payload = aPayload({ corrections: [aCorrection()] });

    const after = withCorrection(payload, aCorrection({ value: "Ada M. Mbeki" }));

    expect(after?.corrections).toHaveLength(1);
    expect(after?.corrections[0].value).toBe("Ada M. Mbeki");
  });

  it("leaves a correction to another field of the same subject alone", () => {
    // Correcting a name and correcting a note are two decisions, and the second must not
    // discard the first.
    const payload = aPayload({ corrections: [aCorrection()] });

    const after = withCorrection(payload, aCorrection({ field: "notes", value: "the aunt" }));

    expect(after?.corrections.map((entry) => entry.field).sort()).toEqual(["name", "notes"]);
  });

  it("does nothing before the payload has loaded", () => {
    expect(withCorrection(null, aCorrection())).toBeNull();
  });
});

describe("showing what the reading currently says", () => {
  it("reads a field off the selected subject", () => {
    expect(currentText(aDocument(), { kind: "character", id: "char:a" }, "name")).toBe("Ada");
  });

  it("renders a list as a person would write one", () => {
    expect(currentText(aDocument(), { kind: "character", id: "char:a" }, "aliases")).toBe("Miss A");
    expect(describeValue(["kinship", "estrangement"])).toBe("kinship, estrangement");
  });

  it("shows an absent field as empty rather than as the word undefined", () => {
    // The run said nothing, and a box that starts empty is the honest way to show it.
    expect(currentText(aDocument(), { kind: "relation", id: "rel:a--b" }, "types")).toBe("");
    expect(describeValue(undefined)).toBe("");
    expect(describeValue(null)).toBe("");
  });

  it("renders a boolean as the word the form uses", () => {
    expect(describeValue(false)).toBe("false");
  });
});

describe("whether this reading already carries a correction", () => {
  it("is false for the reading it was made against", () => {
    // A correction is written in when a snapshot is built, so the one it was made on never
    // has it. Saying otherwise would tell somebody it had already taken effect.
    const entry = aCorrection({ field: "name", value: "Ada Mbeki" });
    expect(appliedIn(aDocument(), { kind: "character", id: "char:a" }, entry)).toBe(false);
  });

  it("is true once a later reading carries it", () => {
    const entry = aCorrection({ field: "name", value: "Ada" });
    expect(appliedIn(aDocument(), { kind: "character", id: "char:a" }, entry)).toBe(true);
  });

  it("compares lists by their contents", () => {
    expect(
      appliedIn(
        aDocument(),
        { kind: "character", id: "char:a" },
        aCorrection({
          field: "aliases",
          value: ["Miss A"],
        }),
      ),
    ).toBe(true);
    expect(
      appliedIn(
        aDocument(),
        { kind: "character", id: "char:a" },
        aCorrection({
          field: "aliases",
          value: ["Miss A", "Ada M"],
        }),
      ),
    ).toBe(false);
  });

  it("treats a cleared field and an absent one as the same state", () => {
    // Clearing a note removes the key, so a document that no longer carries the field agrees
    // with the correction that cleared it.
    expect(sameValue(undefined, "")).toBe(true);
    expect(sameValue(undefined, [])).toBe(true);
    expect(sameValue(null, "")).toBe(true);
    expect(sameValue("", "a note")).toBe(false);
  });

  it("is false when the subject is not in this reading at all", () => {
    expect(appliedIn(aDocument(), { kind: "character", id: "char:gone" }, aCorrection())).toBe(
      false,
    );
  });
});

describe("parsing what a box holds", () => {
  it("splits a list field on commas and drops the blanks", () => {
    expect(parseValue("aliases", " Lizzy , , Eliza ")).toEqual(["Lizzy", "Eliza"]);
    expect(parseValue("types", "kinship")).toEqual(["kinship"]);
  });

  it("keeps a number a number", () => {
    // A valence sent as "-0.4" would reach the schema as a string and be rejected there.
    expect(parseValue("valence", " -0.4 ")).toBe(-0.4);
  });

  it("keeps a boolean a boolean", () => {
    expect(parseValue("directed", "true")).toBe(true);
    expect(parseValue("directed", "false")).toBe(false);
  });

  it("trims a plain string", () => {
    expect(parseValue("name", "  Ada Mbeki  ")).toBe("Ada Mbeki");
  });
});

describe("whether it may be sent", () => {
  it("refuses a blank name", () => {
    expect(canSubmit(aPayload(), "name", "   ")).toBe(false);
    expect(canSubmit(aPayload(), "name", "Ada")).toBe(true);
  });

  it("refuses a kind the schema does not know", () => {
    expect(canSubmit(aPayload(), "kind", "protagonist")).toBe(false);
    expect(canSubmit(aPayload(), "kind", "collective")).toBe(true);
  });

  it("refuses a valence off the scale or not a number", () => {
    expect(canSubmit(aPayload(), "valence", "4")).toBe(false);
    expect(canSubmit(aPayload(), "valence", "warm")).toBe(false);
    expect(canSubmit(aPayload(), "valence", "-0.4")).toBe(true);
  });

  it("allows emptying the fields that can be emptied", () => {
    // Clearing a note and removing an alias list are both real corrections.
    expect(canSubmit(aPayload(), "notes", "")).toBe(true);
    expect(canSubmit(aPayload(), "aliases", "")).toBe(true);
  });
});

describe("the request body", () => {
  it("carries the value in its own type", () => {
    const body = correctionBody({ kind: "relation", id: "rel:a--b" }, "types", "kinship, feud", "");

    expect(body).toEqual({
      kind: "relation",
      id: "rel:a--b",
      field: "types",
      value: ["kinship", "feud"],
    });
    expect("note" in body).toBe(false);
  });

  it("sends a note when there is one, trimmed", () => {
    const body = correctionBody({ kind: "character", id: "char:a" }, "name", "Ada", "  typo  ");
    expect(body.note).toBe("typo");
  });
});

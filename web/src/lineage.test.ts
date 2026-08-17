import { describe, expect, it } from "vitest";

import {
  buildGrid,
  describeDifference,
  differenceBetween,
  hasHistory,
  readingLabels,
  revisionName,
  runName,
  type Lineage,
  type LineageSnapshot,
} from "./lineage.js";

function aRevision(id: string, label: string | null = null) {
  return { id, label, created_at: "2026-01-01T00:00:00Z", sha256: "a".repeat(64), documents: 3 };
}

function aRun(id: string, prompt = "extract-v2", configuration?: string) {
  return {
    id,
    model: "claude-opus-5",
    provider: "anthropic",
    prompt_version: prompt,
    started_at: "2026-01-01T00:00:00Z",
    // Defaults to one configuration per prompt version, which is the common case.
    configuration: configuration ?? `cfg-${prompt}`,
  };
}

function aSnapshot(id: string, revision: string, run: string): LineageSnapshot {
  return {
    id,
    label: null,
    created_at: "2026-01-01T00:00:00Z",
    text_revision_id: revision,
    analysis_run_id: run,
    characters: 4,
    relations: 3,
  };
}

function aLineage(overrides: Partial<Lineage> = {}): Lineage {
  return {
    work: { id: "work:1", title: "A Work", creator: null, collection_id: "col:1" },
    text_revisions: [aRevision("rev:1", "First draft"), aRevision("rev:2", "Second draft")],
    analysis_runs: [aRun("run:1"), aRun("run:2", "extract-v3")],
    snapshots: [
      aSnapshot("snap:a", "rev:1", "run:1"),
      aSnapshot("snap:b", "rev:2", "run:1"),
      aSnapshot("snap:c", "rev:2", "run:2"),
    ],
    ...overrides,
  };
}

describe("buildGrid", () => {
  it("gives a row per text revision and a column per analysis run", () => {
    const grid = buildGrid(aLineage());

    expect(grid.rows).toHaveLength(2);
    expect(grid.rows[0]).toHaveLength(2);
  });

  it("places each snapshot where its two axes meet", () => {
    const grid = buildGrid(aLineage());

    expect(grid.rows[0][0].snapshots.map((s) => s.id)).toEqual(["snap:a"]);
    expect(grid.rows[1][0].snapshots.map((s) => s.id)).toEqual(["snap:b"]);
    expect(grid.rows[1][1].snapshots.map((s) => s.id)).toEqual(["snap:c"]);
  });

  it("leaves a cell empty where that pairing was never analysed", () => {
    // The gap is information: "never tried" is not "tried and identical", and a list of
    // what exists cannot express the difference at all.
    const grid = buildGrid(aLineage());

    expect(grid.rows[0][1].snapshots).toEqual([]);
    expect(grid.rows[0][1].revision.id).toBe("rev:1");
    expect(grid.rows[0][1].reading.runs[0].id).toBe("run:2");
  });

  it("keeps the axes in the order the work records them", () => {
    const grid = buildGrid(aLineage());

    expect(grid.revisions.map((r) => r.id)).toEqual(["rev:1", "rev:2"]);
    expect(grid.readings.map((reading) => reading.runs[0].id)).toEqual(["run:1", "run:2"]);
  });

  it("sets aside a snapshot naming a revision the work does not list", () => {
    // Should not happen; both axes are recorded when the snapshot is written. If it does,
    // a graph silently missing from the list is worse than one that must be explained.
    const lineage = aLineage();
    lineage.snapshots.push(aSnapshot("snap:ghost", "rev:gone", "run:1"));

    const grid = buildGrid(lineage);
    expect(grid.orphaned.map((s) => s.id)).toEqual(["snap:ghost"]);
    expect(grid.rows.flat().flatMap((cell) => cell.snapshots)).toHaveLength(3);
  });

  it("sets aside a snapshot naming a run the work does not list", () => {
    const lineage = aLineage();
    lineage.snapshots.push(aSnapshot("snap:ghost", "rev:1", "run:gone"));

    expect(buildGrid(lineage).orphaned.map((s) => s.id)).toEqual(["snap:ghost"]);
  });

  it("tolerates more than one snapshot at an intersection", () => {
    const lineage = aLineage();
    lineage.snapshots.push(aSnapshot("snap:twin", "rev:1", "run:1"));

    expect(buildGrid(lineage).rows[0][0].snapshots.map((s) => s.id)).toEqual([
      "snap:a",
      "snap:twin",
    ]);
  });

  it("puts two executions of one configuration in a single column", () => {
    // The case that decides the whole shape. A run identifier includes when it ran, so
    // re-analysing a second draft with identical settings is a *different run* — but it is
    // the same reading, and only one column can let a reader hold the analysis still and
    // see what the text did.
    const lineage = aLineage({
      analysis_runs: [
        aRun("run:monday", "extract-v2", "same"),
        aRun("run:tuesday", "extract-v2", "same"),
      ],
      snapshots: [
        aSnapshot("snap:a", "rev:1", "run:monday"),
        aSnapshot("snap:b", "rev:2", "run:tuesday"),
      ],
    });

    const grid = buildGrid(lineage);

    expect(grid.readings).toHaveLength(1);
    expect(grid.readings[0].runs.map((run) => run.id)).toEqual(["run:monday", "run:tuesday"]);
    expect(grid.rows[0][0].snapshots.map((s) => s.id)).toEqual(["snap:a"]);
    expect(grid.rows[1][0].snapshots.map((s) => s.id)).toEqual(["snap:b"]);
  });

  it("keeps configurations apart even when they share a prompt version", () => {
    // Two readings can call themselves extract-v2 and differ in effort or window size.
    const lineage = aLineage({
      analysis_runs: [aRun("run:1", "extract-v2", "cfg-a"), aRun("run:2", "extract-v2", "cfg-b")],
      snapshots: [aSnapshot("snap:a", "rev:1", "run:1"), aSnapshot("snap:b", "rev:1", "run:2")],
    });

    expect(buildGrid(lineage).readings).toHaveLength(2);
  });

  it("handles a work with one revision and one run", () => {
    const grid = buildGrid(
      aLineage({
        text_revisions: [aRevision("rev:1")],
        analysis_runs: [aRun("run:1")],
        snapshots: [aSnapshot("snap:a", "rev:1", "run:1")],
      }),
    );

    expect(grid.rows).toHaveLength(1);
    expect(grid.rows[0][0].snapshots.map((s) => s.id)).toEqual(["snap:a"]);
  });

  it("handles a work that has been ingested but never analysed", () => {
    const grid = buildGrid(
      aLineage({ analysis_runs: [], snapshots: [], text_revisions: [aRevision("rev:1")] }),
    );

    expect(grid.rows).toEqual([[]]);
    expect(grid.readings).toEqual([]);
    expect(grid.orphaned).toEqual([]);
  });

  it("handles an empty work", () => {
    const grid = buildGrid(aLineage({ text_revisions: [], analysis_runs: [], snapshots: [] }));

    expect(grid.rows).toEqual([]);
  });
});

describe("differenceBetween", () => {
  it("names the text when only the revision differs", () => {
    // Reading down a column: the analysis is held still, so the work is what moved.
    const difference = differenceBetween(
      aSnapshot("a", "rev:1", "run:1"),
      aSnapshot("b", "rev:2", "run:1"),
    );

    expect(difference).toBe("text");
    expect(describeDifference(difference)).toContain("the work changed");
  });

  it("names the analysis when only the run differs", () => {
    const difference = differenceBetween(
      aSnapshot("a", "rev:1", "run:1"),
      aSnapshot("b", "rev:1", "run:2"),
    );

    expect(difference).toBe("analysis");
    expect(describeDifference(difference)).toContain("the reading changed");
  });

  it("credits neither when both differ", () => {
    // The case Invariant 4 exists to keep visible. Picking whichever changed more would be
    // inventing an attribution the evidence does not support.
    const difference = differenceBetween(
      aSnapshot("a", "rev:1", "run:1"),
      aSnapshot("b", "rev:2", "run:2"),
    );

    expect(difference).toBe("both");
    expect(describeDifference(difference)).toContain("neither can be credited");
  });

  it("says so when the two axes agree", () => {
    expect(
      differenceBetween(aSnapshot("a", "rev:1", "run:1"), aSnapshot("b", "rev:1", "run:1")),
    ).toBe("same");
  });
});

describe("naming the axes", () => {
  it("prefers a revision's label", () => {
    expect(revisionName(aRevision("rev:1", "First draft"))).toBe("First draft");
  });

  it("falls back to the identifier when a revision has no label", () => {
    expect(revisionName(aRevision("rev:abc123"))).toBe("rev:abc123");
  });

  it("ignores a label that is only whitespace", () => {
    expect(revisionName(aRevision("rev:abc123", "   "))).toBe("rev:abc123");
  });

  it("names a run by what makes one reading differ from another", () => {
    // Not the identifier: it says nothing about why this graph is not the one beside it.
    expect(runName(aRun("run:1", "extract-v2"))).toBe("claude-opus-5 · extract-v2");
  });
});

describe("hasHistory", () => {
  it("is false for a work analysed once", () => {
    const grid = buildGrid(
      aLineage({
        text_revisions: [aRevision("rev:1")],
        analysis_runs: [aRun("run:1")],
        snapshots: [aSnapshot("snap:a", "rev:1", "run:1")],
      }),
    );

    expect(hasHistory(grid)).toBe(false);
  });

  it("is true once either axis has more than one point", () => {
    expect(hasHistory(buildGrid(aLineage()))).toBe(true);
  });
});

describe("readingLabels", () => {
  it("uses the plain name when it is already unique", () => {
    const grid = buildGrid(aLineage());
    const labels = readingLabels(grid.readings);

    expect(labels.get("cfg-extract-v2")).toBe("claude-opus-5 · extract-v2");
  });

  it("disambiguates two readings that would otherwise read identically", () => {
    // Two readings can differ only in an effort level or a window size. Two columns with
    // the same caption read as a duplicate rather than as a distinction.
    const grid = buildGrid(
      aLineage({
        analysis_runs: [
          aRun("run:1", "extract-v2", "aaaaaaaaaaaa"),
          aRun("run:2", "extract-v2", "bbbbbbbbbbbb"),
        ],
        snapshots: [],
      }),
    );
    const labels = readingLabels(grid.readings);

    expect(labels.get("aaaaaaaaaaaa")).toBe("claude-opus-5 · extract-v2 (aaaaaa)");
    expect(labels.get("bbbbbbbbbbbb")).toBe("claude-opus-5 · extract-v2 (bbbbbb)");
    expect(new Set(labels.values()).size).toBe(2);
  });

  it("leaves genuinely different names alone", () => {
    const grid = buildGrid(aLineage());
    const labels = readingLabels(grid.readings);

    expect([...labels.values()].every((label) => !label.includes("("))).toBe(true);
  });
});

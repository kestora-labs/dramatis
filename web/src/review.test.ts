import { describe, expect, it } from "vitest";

import {
  DEFAULT_STATUS,
  STATUSES,
  canRecord,
  decisionBody,
  declaredStatus,
  indexReviews,
  keyOf,
  reviewsUrl,
  statusFor,
  tally,
  withSubject,
  type ReviewOverlay,
  type ReviewSubject,
} from "./review.js";
import type { SnapshotDocument } from "./graph.js";

function aDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
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
      { id: "char:a", name: "Ada", provenance: "observed" },
      { id: "char:b", name: "Bram", provenance: "observed", review_status: "accepted" },
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
    ...overrides,
  } as SnapshotDocument;
}

function aSubject(overrides: Partial<ReviewSubject> = {}): ReviewSubject {
  return {
    kind: "character",
    id: "char:a",
    label: "Ada",
    status: "accepted",
    note: null,
    decided_at: "2026-01-01T00:00:00+00:00",
    decided_in: "snap:1",
    reviewed: true,
    ...overrides,
  };
}

function anOverlay(subjects: ReviewSubject[]): ReviewOverlay {
  const counts = { proposed: 0, accepted: 0, corrected: 0, rejected: 0 };
  for (const subject of subjects) counts[subject.status] += 1;
  return {
    snapshot_id: "snap:1",
    work_id: "work:1",
    counts,
    reviewed: subjects.filter((subject) => subject.reviewed).length,
    subjects,
  };
}

describe("the vocabulary", () => {
  it("is the four the schema and the server name, in the order a claim travels", () => {
    expect([...STATUSES]).toEqual(["proposed", "accepted", "corrected", "rejected"]);
    expect(DEFAULT_STATUS).toBe("proposed");
  });
});

describe("addressing a subject", () => {
  it("keys on the kind as well as the id", () => {
    // Ids are unique within a kind and not across one, so a map keyed on the bare id lets a
    // character and a relation overwrite each other's status.
    expect(keyOf("character", "x")).not.toBe(keyOf("relation", "x"));
  });

  it("escapes the snapshot in the URL it asks for", () => {
    expect(reviewsUrl("snap:a b")).toBe("/api/snapshots/snap%3Aa%20b/reviews");
  });
});

describe("indexing what the server served", () => {
  it("makes every subject findable by its selection", () => {
    const index = indexReviews(
      anOverlay([aSubject(), aSubject({ kind: "relation", id: "rel:x" })]),
    );

    expect(index[keyOf("character", "char:a")]?.status).toBe("accepted");
    expect(index[keyOf("relation", "rel:x")]?.status).toBe("accepted");
  });

  it("survives a snapshot whose reviews have not loaded", () => {
    expect(indexReviews(null)).toEqual({});
  });
});

describe("folding in a fresh decision", () => {
  it("replaces what it supersedes rather than appending beside it", () => {
    const overlay = anOverlay([aSubject(), aSubject({ id: "char:b", label: "Bram" })]);
    const after = withSubject(overlay, aSubject({ status: "rejected", note: "not in the book" }));

    expect(after?.subjects).toHaveLength(2);
    const index = indexReviews(after);
    expect(index[keyOf("character", "char:a")]?.status).toBe("rejected");
    expect(index[keyOf("character", "char:a")]?.note).toBe("not in the book");
  });

  it("recomputes the tally from the subjects, so the two cannot drift", () => {
    const overlay = anOverlay([aSubject(), aSubject({ id: "char:b", label: "Bram" })]);
    const after = withSubject(overlay, aSubject({ status: "rejected" }));

    expect(after?.counts.accepted).toBe(1);
    expect(after?.counts.rejected).toBe(1);
    expect(after?.reviewed).toBe(2);
  });

  it("does nothing before the overlay has loaded", () => {
    expect(withSubject(null, aSubject())).toBeNull();
  });
});

describe("where review of the selection stands", () => {
  it("prefers a recorded decision to what the document declared", () => {
    const document = aDocument();
    const index = indexReviews(
      anOverlay([aSubject({ id: "char:b", label: "Bram", status: "rejected" })]),
    );

    const found = statusFor(index, document, { kind: "character", id: "char:b" });
    expect(found?.status).toBe("rejected");
    expect(found?.reviewed).toBe(true);
  });

  it("falls back to what the document declared when nobody has ruled", () => {
    // The snapshot may carry a review_status of its own; that is the starting point, and
    // it is not the same fact as a person having decided.
    const found = statusFor({}, aDocument(), { kind: "character", id: "char:b" });

    expect(found?.status).toBe("accepted");
    expect(found?.reviewed).toBe(false);
  });

  it("treats a subject the document says nothing about as proposed", () => {
    expect(statusFor({}, aDocument(), { kind: "relation", id: "rel:a--b" })?.status).toBe(
      "proposed",
    );
    expect(declaredStatus(aDocument(), { kind: "character", id: "char:a" })).toBe("proposed");
  });

  it("ignores a status the vocabulary does not contain", () => {
    const document = aDocument({
      characters: [
        { id: "char:a", name: "Ada", provenance: "observed", review_status: "probably-fine" },
      ],
    } as Partial<SnapshotDocument>);

    expect(declaredStatus(document, { kind: "character", id: "char:a" })).toBe("proposed");
  });

  it("is null when nothing is selected", () => {
    expect(statusFor({}, aDocument(), null)).toBeNull();
  });
});

describe("the snapshot-wide tally", () => {
  it("names every status, including the ones at zero", () => {
    // A missing entry reads as "not applicable" when what is meant is "none yet".
    const counted = tally(anOverlay([aSubject()]));

    expect(counted.map((entry) => entry.status)).toEqual([...STATUSES]);
    expect(counted.find((entry) => entry.status === "rejected")?.count).toBe(0);
    expect(counted.find((entry) => entry.status === "accepted")?.count).toBe(1);
  });

  it("reports zeroes rather than nothing before the overlay has loaded", () => {
    expect(tally(null).every((entry) => entry.count === 0)).toBe(true);
  });
});

describe("recording a decision", () => {
  it("will not correct without saying what was corrected", () => {
    // The server refuses this too. Checking here is so the button is disabled rather than
    // the click producing an error.
    expect(canRecord("corrected", "   ")).toBe(false);
    expect(canRecord("corrected", "this is the housekeeper")).toBe(true);
  });

  it("lets every other status stand on its own", () => {
    expect(canRecord("accepted", "")).toBe(true);
    expect(canRecord("rejected", "")).toBe(true);
    expect(canRecord("proposed", "")).toBe(true);
  });

  it("sends an empty note as absent rather than as an empty string", () => {
    const body = decisionBody({ kind: "relation", id: "rel:a--b" }, "accepted", "  ");

    expect(body).toEqual({ kind: "relation", id: "rel:a--b", status: "accepted" });
    expect("note" in body).toBe(false);
  });

  it("trims a note it does send", () => {
    const body = decisionBody({ kind: "character", id: "char:a" }, "corrected", "  wrong name  ");
    expect(body.note).toBe("wrong name");
  });
});

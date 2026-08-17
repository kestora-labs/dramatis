import { describe, expect, it } from "vitest";

import { documentOrder, formatLocator, listEvidence, orderEvidence } from "./evidence.js";
import type { SnapshotDocument, SnapshotEvidence } from "./graph.js";

function aDocument(overrides: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "0.1.0",
    collection: { id: "col:test", name: "Test" },
    works: [{ id: "work:1", title: "A Work" }],
    documents: [{ id: "doc:one", work_id: "work:1", title: "The Only Book", role: "narrative" }],
    analysis_runs: [{ id: "run:1", model: "m", prompt_version: "extract-v2" }],
    snapshot: { id: "snap:1", text_revision_id: "rev:1", analysis_run_id: "run:1" },
    characters: [],
    relations: [],
    ...overrides,
  };
}

/** Evidence in one document, at `chapter <index>`, quoting `exact`. */
function at(index: number | undefined, exact: string, extra: Partial<SnapshotEvidence> = {}) {
  return {
    selector: { exact },
    locator: {
      document_id: "doc:one",
      path: index === undefined ? [{ type: "chapter" }] : [{ type: "chapter", index }],
    },
    ...extra,
  } satisfies SnapshotEvidence;
}

function quotations(evidence: SnapshotEvidence[]): string[] {
  return evidence.map((piece) => piece.selector.exact);
}

describe("orderEvidence", () => {
  it("puts passages in the order they occur in the work", () => {
    const document = aDocument();
    const ordered = orderEvidence(document, [at(34, "third"), at(3, "first"), at(11, "second")]);

    expect(quotations(ordered)).toEqual(["first", "second", "third"]);
  });

  it("does not mutate the array it was given", () => {
    const document = aDocument();
    const original = [at(9, "b"), at(1, "a")];
    orderEvidence(document, original);

    expect(quotations(original)).toEqual(["b", "a"]);
  });

  it("orders by document first, in corpus order", () => {
    // A second volume's chapter 1 follows the first volume's chapter 40, not precedes it.
    const document = aDocument({
      documents: [
        { id: "doc:one", work_id: "work:1", title: "Volume I", role: "narrative" },
        { id: "doc:two", work_id: "work:1", title: "Volume II", role: "narrative" },
      ],
    });
    const second: SnapshotEvidence = {
      selector: { exact: "opens volume two" },
      locator: { document_id: "doc:two", path: [{ type: "chapter", index: 1 }] },
    };

    const ordered = orderEvidence(document, [second, at(40, "closes volume one")]);
    expect(quotations(ordered)).toEqual(["closes volume one", "opens volume two"]);
  });

  it("descends the path, outermost segment first", () => {
    const document = aDocument();
    const deep = (part: number, chapter: number, exact: string): SnapshotEvidence => ({
      selector: { exact },
      locator: {
        document_id: "doc:one",
        path: [
          { type: "part", index: part },
          { type: "chapter", index: chapter },
        ],
      },
    });

    const ordered = orderEvidence(document, [deep(2, 1, "later"), deep(1, 9, "earlier")]);
    expect(quotations(ordered)).toEqual(["earlier", "later"]);
  });

  it("puts a container before what it contains", () => {
    const document = aDocument();
    const whole: SnapshotEvidence = {
      selector: { exact: "the part itself" },
      locator: { document_id: "doc:one", path: [{ type: "part", index: 1 }] },
    };
    const within: SnapshotEvidence = {
      selector: { exact: "a chapter inside it" },
      locator: {
        document_id: "doc:one",
        path: [
          { type: "part", index: 1 },
          { type: "chapter", index: 2 },
        ],
      },
    };

    expect(quotations(orderEvidence(document, [within, whole]))).toEqual([
      "the part itself",
      "a chapter inside it",
    ]);
  });

  it("places what it can place, and sends the rest to the end", () => {
    // A segment with no ordinal has no position to sort on. It goes last rather than
    // being guessed at, and rather than blocking the passages that can be placed.
    const document = aDocument();
    const ordered = orderEvidence(document, [
      at(undefined, "unplaceable"),
      at(7, "second"),
      at(2, "first"),
    ]);

    expect(quotations(ordered)).toEqual(["first", "second", "unplaceable"]);
  });

  it("sends evidence naming no document after evidence that names one", () => {
    const document = aDocument();
    const homeless: SnapshotEvidence = {
      selector: { exact: "no document" },
      locator: { path: [{ type: "chapter", index: 1 }] },
    };

    // Chapter 1, but unplaceable among documents, so it cannot be interleaved.
    expect(quotations(orderEvidence(document, [homeless, at(40, "chapter forty")]))).toEqual([
      "chapter forty",
      "no document",
    ]);
  });

  it("has no effect when a single-document work names no document anywhere", () => {
    const document = aDocument();
    const bare = (index: number, exact: string): SnapshotEvidence => ({
      selector: { exact },
      locator: { path: [{ type: "chapter", index }] },
    });

    expect(quotations(orderEvidence(document, [bare(5, "later"), bare(1, "earlier")]))).toEqual([
      "earlier",
      "later",
    ]);
  });

  it("breaks a tie on the offset hint when structure has run out", () => {
    const document = aDocument();
    const ordered = orderEvidence(document, [
      at(3, "later in the chapter", { selector: { exact: "later in the chapter", start: 900 } }),
      at(3, "earlier in the chapter", { selector: { exact: "earlier in the chapter", start: 20 } }),
    ]);

    expect(quotations(ordered)).toEqual(["earlier in the chapter", "later in the chapter"]);
  });

  it("keeps the stored order when two passages cannot be told apart", () => {
    // Same segment, no offsets. Inventing a sequence here — alphabetical, by length —
    // would look like narrative order and would not be.
    const document = aDocument();
    const ordered = orderEvidence(document, [at(3, "stored first"), at(3, "stored second")]);

    expect(quotations(ordered)).toEqual(["stored first", "stored second"]);
  });

  it("handles an empty list", () => {
    expect(orderEvidence(aDocument(), [])).toEqual([]);
  });
});

describe("documentOrder", () => {
  it("ranks documents by their position in the corpus", () => {
    const order = documentOrder(
      aDocument({
        documents: [
          { id: "doc:a", work_id: "work:1", role: "narrative" },
          { id: "doc:b", work_id: "work:1", role: "narrative" },
        ],
      }),
    );

    expect(order.get("doc:a")).toBe(0);
    expect(order.get("doc:b")).toBe(1);
  });

  it("copes with a snapshot listing no documents", () => {
    expect(documentOrder(aDocument({ documents: undefined })).size).toBe(0);
  });
});

describe("formatLocator", () => {
  it("reads a segment as the work declared it", () => {
    // The type is data, not an enumeration, so it is printed rather than translated.
    expect(formatLocator(at(3, "q"))).toBe("chapter 3");
    expect(
      formatLocator({
        selector: { exact: "q" },
        locator: { path: [{ type: "section", index: 30 }] },
      }),
    ).toBe("section 30");
  });

  it("joins a nested path from the outside in", () => {
    expect(
      formatLocator({
        selector: { exact: "q" },
        locator: {
          path: [
            { type: "part", index: 2 },
            { type: "chapter", index: 7 },
          ],
        },
      }),
    ).toBe("part 2 › chapter 7");
  });

  it("adds a label where a segment has one", () => {
    expect(
      formatLocator({
        selector: { exact: "q" },
        locator: { path: [{ type: "chapter", index: 1, label: "The Assembly" }] },
      }),
    ).toBe("chapter 1 — The Assembly");
  });

  it("names the type alone when there is no ordinal", () => {
    expect(formatLocator(at(undefined, "q"))).toBe("chapter");
  });
});

describe("listEvidence", () => {
  it("returns quotation, note and locator, in reading order", () => {
    const document = aDocument();
    const list = listEvidence(document, [
      at(34, "In vain have I struggled.", { note: "The first proposal." }),
      at(3, "handsome enough to tempt _me_", { note: "The refusal to dance." }),
    ]);

    expect(list).toEqual([
      {
        position: 1,
        locator: "chapter 3",
        quotation: "handsome enough to tempt _me_",
        note: "The refusal to dance.",
      },
      {
        position: 0,
        locator: "chapter 34",
        quotation: "In vain have I struggled.",
        note: "The first proposal.",
      },
    ]);
  });

  it("keeps each entry's position in the stored array, not its place in the sorted list", () => {
    // The server addresses evidence by its stored index. Sending its place in a re-ordered
    // list would open the wrong passage — and would do so silently, since both are numbers
    // in range.
    const list = listEvidence(aDocument(), [at(9, "last"), at(1, "first"), at(5, "middle")]);

    expect(list.map((piece) => piece.quotation)).toEqual(["first", "middle", "last"]);
    expect(list.map((piece) => piece.position)).toEqual([1, 2, 0]);
  });

  it("omits a note the evidence does not carry", () => {
    const [only] = listEvidence(aDocument(), [at(1, "unannotated")]);
    expect(only.note).toBeUndefined();
  });

  it("does not name the document when the work has only one", () => {
    // Repeating one title against every passage hides the position beside it.
    const [only] = listEvidence(aDocument(), [at(1, "q")]);
    expect(only.document).toBeUndefined();
  });

  it("names the document when the work has more than one", () => {
    const document = aDocument({
      documents: [
        { id: "doc:one", work_id: "work:1", title: "Volume I", role: "narrative" },
        { id: "doc:two", work_id: "work:1", title: "Volume II", role: "narrative" },
      ],
    });

    const [only] = listEvidence(document, [at(1, "q")]);
    expect(only.document).toBe("Volume I");
  });

  it("falls back to the document id where the corpus gave no title", () => {
    const document = aDocument({
      documents: [
        { id: "doc:one", work_id: "work:1", role: "narrative" },
        { id: "doc:two", work_id: "work:1", role: "narrative" },
      ],
    });

    expect(listEvidence(document, [at(1, "q")])[0].document).toBe("doc:one");
  });

  it("returns nothing for a relation with no evidence", () => {
    expect(listEvidence(aDocument(), undefined)).toEqual([]);
    expect(listEvidence(aDocument(), [])).toEqual([]);
  });
});

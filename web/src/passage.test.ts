import { describe, expect, it } from "vitest";

import { describeAnchor, passageUrl, splitPassage } from "./passage.js";

const TEXT = "Ada met Bram at the gate, and neither spoke.";

describe("splitPassage", () => {
  it("cuts the passage into before, quotation and after", () => {
    const parts = splitPassage(TEXT, { start: 8, end: 12 });

    expect(parts.before).toBe("Ada met ");
    expect(parts.quoted).toBe("Bram");
    expect(parts.after).toBe(" at the gate, and neither spoke.");
  });

  it("puts the three parts back together as the original", () => {
    const parts = splitPassage(TEXT, { start: 8, end: 12 });
    expect(parts.before + parts.quoted + parts.after).toBe(TEXT);
  });

  it("handles a quotation at the very start", () => {
    const parts = splitPassage(TEXT, { start: 0, end: 3 });

    expect(parts.before).toBe("");
    expect(parts.quoted).toBe("Ada");
  });

  it("handles a quotation running to the very end", () => {
    const parts = splitPassage(TEXT, { start: TEXT.length - 6, end: TEXT.length });

    expect(parts.quoted).toBe("spoke.");
    expect(parts.after).toBe("");
  });

  it("shows the passage unhighlighted when there is no span", () => {
    // The server could not find the quotation. The passage is still worth reading, and
    // recovering the quotation is 2.4.
    const parts = splitPassage(TEXT, null);

    expect(parts.before).toBe(TEXT);
    expect(parts.quoted).toBe("");
    expect(parts.after).toBe("");
  });

  it("refuses a span that does not fit the text it was given", () => {
    // The server measured the span against the text it sent, so this should not happen.
    // Slicing anyway would put a highlight over the wrong words rather than reporting a
    // disagreement between the two.
    for (const span of [
      { start: -1, end: 4 },
      { start: 5, end: TEXT.length + 10 },
      { start: 9, end: 4 },
      { start: 4, end: 4 },
      { start: 1.5, end: 4 },
    ]) {
      const parts = splitPassage(TEXT, span);
      expect(parts.quoted, JSON.stringify(span)).toBe("");
      expect(parts.before, JSON.stringify(span)).toBe(TEXT);
    }
  });

  it("copes with an empty passage", () => {
    expect(splitPassage("", { start: 0, end: 0 })).toEqual({ before: "", quoted: "", after: "" });
  });
});

describe("passageUrl", () => {
  it("addresses a piece of evidence by its stored position", () => {
    expect(passageUrl("snap:1", "rel:a--b", 3)).toBe(
      "/api/snapshots/snap%3A1/passage?relation=rel%3Aa--b&evidence=3",
    );
  });

  it("escapes identifiers rather than trusting them to be url-safe", () => {
    const url = passageUrl("snap:a b", "rel:x&y=1", 0);

    expect(url).toContain("snap%3Aa%20b");
    expect(url).toContain("rel%3Ax%26y%3D1");
  });

  it("carries no part of the quotation", () => {
    // A quotation in a query string puts a manuscript into every access log that sees it.
    const url = passageUrl("snap:1", "rel:a--b", 0);
    expect(url).not.toMatch(/[A-Za-z]{20}/);
  });
});

describe("describeAnchor", () => {
  const verbatim = {
    method: "exact" as const,
    similarity: 1,
    ambiguous: false,
    moved: false,
    stored_path: null,
  };

  it("says nothing when the quotation was found verbatim where it was recorded", () => {
    // The common case. A note on every passage would train the reader to ignore the notes
    // that matter.
    expect(describeAnchor(verbatim, null)).toBeNull();
  });

  it("says an approximate match is approximate, with how close it was", () => {
    const caveat = describeAnchor(
      { ...verbatim, method: "fuzzy", similarity: 0.87, moved: true },
      "chapter 3",
    );

    expect(caveat).toContain("no longer in the text word for word");
    expect(caveat).toContain("87%");
  });

  it("reports an ambiguous match rather than presenting a coin toss as a citation", () => {
    const caveat = describeAnchor({ ...verbatim, method: "context", ambiguous: true }, "chapter 3");
    expect(caveat).toContain("more than one place");
  });

  it("says where the evidence used to point when the passage has moved", () => {
    const caveat = describeAnchor({ ...verbatim, moved: true, stored_path: [] }, "chapter 3");

    expect(caveat).toContain("moved");
    expect(caveat).toContain("chapter 3");
  });

  it("still reports a move when it cannot name the old position", () => {
    const caveat = describeAnchor({ ...verbatim, moved: true }, null);

    expect(caveat).toContain("moved");
    expect(caveat).not.toContain("null");
  });

  it("prefers the strongest caveat when more than one applies", () => {
    // A fuzzy match has almost always moved as well. Saying both would bury the one that
    // changes how much the highlight is worth.
    const caveat = describeAnchor(
      { ...verbatim, method: "fuzzy", similarity: 0.9, ambiguous: true, moved: true },
      "chapter 3",
    );

    expect(caveat).toContain("word for word");
    expect(caveat).not.toContain("more than one place");
  });
});

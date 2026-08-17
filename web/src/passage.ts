/**
 * The source text behind a piece of evidence, ready to render.
 *
 * The server does the finding. It holds the text, and `dramatis.text` holds Invariant 3's
 * definition of "verbatim" — whitespace-normalised on both sides, nothing else altered.
 * Searching for the quotation here instead would put a second copy of that rule in a second
 * language, and the copy nobody tests is the one that drifts. So this module receives a
 * passage and a span and only decides how to cut it into three.
 *
 * The cut is by offsets rather than by marking up the text, so no manuscript ever passes
 * through a string-replacement step on its way to the DOM.
 */

/** Which rung of the re-anchoring ladder found the quotation. */
export interface AnchorReport {
  method: "exact" | "context" | "fuzzy";
  similarity: number;
  ambiguous: boolean;
  /** True when the quotation is no longer at the position the evidence recorded. */
  moved: boolean;
  stored_path: { type: string; index?: number; label?: string }[] | null;
}

/** What `GET /api/snapshots/{id}/passage` returns. */
export interface PassageResponse {
  document_id: string | null;
  path: { type: string; index?: number; label?: string }[];
  text: string;
  /** Null when the quotation could not be found anywhere in the revision. */
  quotation: { start: number; end: number } | null;
  /** True when the window grew past the named passage to hold the whole quotation. */
  widened: boolean;
  text_revision_id: string;
  anchor: AnchorReport;
}

/**
 * What to tell the reader about a highlight they are looking at.
 *
 * Returns null when there is nothing worth saying, which is the common case: the quotation
 * was found verbatim, exactly where the evidence recorded it. Everything else is a weaker
 * claim than that, and the difference is the reader's to judge rather than the view's to
 * smooth over — an approximate match drawn identically to a verbatim one is a citation
 * nobody can check.
 */
export function describeAnchor(anchor: AnchorReport, moved: string | null): string | null {
  if (anchor.method === "fuzzy") {
    const percent = Math.round(anchor.similarity * 100);
    return (
      `This quotation is no longer in the text word for word — the closest passage is ` +
      `shown, ${percent}% of a match. The text has been edited since this snapshot was made.`
    );
  }

  if (anchor.ambiguous) {
    return (
      "This quotation appears in more than one place and nothing stored with it says " +
      "which was meant. One of them is shown."
    );
  }

  if (anchor.moved) {
    return moved === null
      ? "This passage has moved since the snapshot was made."
      : `This passage has moved since the snapshot was made; the evidence recorded it at ${moved}.`;
  }

  return null;
}

/** A passage cut into the part before the quotation, the quotation, and the part after. */
export interface PassageParts {
  before: string;
  quoted: string;
  after: string;
}

/**
 * Split a passage around its quotation.
 *
 * A span that does not fit the text yields the whole passage and no highlight. That should
 * not happen — the server measured the span against the text it sent — but rendering an
 * empty `<mark>` in the middle of a sentence, or slicing at a negative index, would turn a
 * disagreement between the two into a silent visual lie.
 */
export function splitPassage(
  text: string,
  span: { start: number; end: number } | null,
): PassageParts {
  if (
    span === null ||
    !Number.isInteger(span.start) ||
    !Number.isInteger(span.end) ||
    span.start < 0 ||
    span.end > text.length ||
    span.end <= span.start
  ) {
    return { before: text, quoted: "", after: "" };
  }

  return {
    before: text.slice(0, span.start),
    quoted: text.slice(span.start, span.end),
    after: text.slice(span.end),
  };
}

/**
 * The passage endpoint's address.
 *
 * `position` is the piece's index in the stored evidence array, not its place in the
 * reading-ordered list the panel shows. Nothing about the text travels in the URL: a
 * quotation in a query string would put lines of an unpublished manuscript into every
 * access log that saw the request.
 */
export function passageUrl(snapshotId: string, relationId: string, position: number): string {
  const query = new URLSearchParams({ relation: relationId, evidence: String(position) });
  return `/api/snapshots/${encodeURIComponent(snapshotId)}/passage?${query}`;
}

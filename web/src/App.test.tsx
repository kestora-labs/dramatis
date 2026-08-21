// @vitest-environment jsdom

/**
 * What the detail panel does when the selection changes underneath it.
 *
 * The one test in the client that renders. Everything else about the panel is decided in
 * `detail.ts`, `review.ts` and `correction.ts` and tested there without a DOM — but the
 * fault this covers was invisible to all of it, because it was not in what the panel
 * decided to show. It was in what React did with the children when the decision changed:
 * two sibling controls carrying the same `key` made one of them un-deletable, and every
 * selection change left another review control behind.
 *
 * So the assertion is a count of elements after a *transition*, and it takes at least two
 * selections to make. Rendering one subject and counting has never failed.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DetailPanel } from "./App.js";
import type { Detail, Selection } from "./detail.js";
import type { CorrectionsPayload } from "./correction.js";
import type { ReviewSubject } from "./review.js";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CORRECTABLE: CorrectionsPayload = {
  snapshot_id: "snap:1",
  work_id: "work:1",
  corrections: [],
  conflicts: [],
  correctable: { character: ["name", "kind"], relation: ["types"] },
  character_kinds: ["person"],
};

/** Three subjects, so a run of selections is a run of genuinely different ones. */
const SUBJECTS: { selection: Selection; detail: Detail }[] = [
  {
    selection: { kind: "character", id: "char:a" },
    detail: {
      kind: "character",
      title: "Ada",
      aliases: ["Ada L."],
      fields: [{ label: "Identifier", value: "char:a", code: true }],
    },
  },
  {
    selection: { kind: "relation", id: "rel:a--b" },
    detail: {
      kind: "relation",
      title: "Ada — Bram",
      types: ["kinship"],
      evidence: [],
      fields: [{ label: "Identifier", value: "rel:a--b", code: true }],
    },
  },
  {
    selection: { kind: "character", id: "char:b" },
    detail: {
      kind: "character",
      title: "Bram",
      aliases: [],
      fields: [{ label: "Identifier", value: "char:b", code: true }],
    },
  },
];

function aReview(selection: Selection): ReviewSubject {
  return {
    kind: selection.kind,
    id: selection.id,
    label: selection.id,
    status: "proposed",
    note: null,
    decided_at: null,
    decided_in: null,
    reviewed: false,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** Render the panel over one subject, as clicking that node or edge would. */
function select(at: number): void {
  const { selection, detail } = SUBJECTS[at]!;
  act(() =>
    root.render(
      <DetailPanel
        detail={detail}
        selection={selection}
        document_={null}
        review={aReview(selection)}
        reviewing="snap:1"
        reviewBusy={false}
        onReview={() => {}}
        corrections={CORRECTABLE}
        correctionBusy={false}
        onCorrect={() => {}}
        onClear={() => {}}
        onOpen={() => {}}
        openAt={null}
      />,
    ),
  );
}

function count(selector: string): number {
  return container.querySelectorAll(selector).length;
}

describe("the detail panel across a change of selection", () => {
  it("holds one review control after each of a run of selections", () => {
    select(0);
    expect(count("section.review")).toBe(1);

    select(1);
    expect(count("section.review")).toBe(1);

    select(2);
    expect(count("section.review")).toBe(1);
  });

  it("holds one correction control, and one panel, across the same run", () => {
    select(0);
    select(1);
    select(2);

    expect(count("section.correction")).toBe(1);
    expect(count("section.detail")).toBe(1);
  });

  it("shows the newly selected subject and nothing of the last one", () => {
    select(0);
    select(1);

    expect(container.querySelectorAll("h2")).toHaveLength(1);
    expect(container.querySelector("h2")?.textContent).toBe("Ada — Bram");
  });
});

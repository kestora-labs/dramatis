import { describe, expect, it } from "vitest";

import {
  applyMarks,
  compareProvenance,
  describeComparison,
  findingList,
  marksFor,
  type Agreement,
} from "./declared.js";
import type { GraphElement, SnapshotDocument, SnapshotRelation } from "./graph.js";

/**
 * Fixture C's two findings, in miniature.
 *
 * Ada and Tomas are declared estranged siblings and never share a scene. Ada and Yeong carry
 * the page time and the bible does not mention them. Ada and Bram are both declared and
 * enacted, which is the uninteresting case that must not crowd out the other two.
 */
function aRelation(over: Partial<SnapshotRelation> = {}): SnapshotRelation {
  return {
    id: "rel:x--y",
    source: "char:x",
    target: "char:y",
    weight: 1,
    weight_basis: "interaction_passages",
    provenance: "observed",
    ...over,
  };
}

function aDocument(over: Partial<SnapshotDocument> = {}): SnapshotDocument {
  return {
    schema_version: "1.0.0",
    collection: { id: "col:c", name: "Kanto" },
    works: [],
    documents: [],
    text_revisions: [],
    analysis_runs: [],
    snapshot: {
      id: "snap:1",
      work_id: "work:1",
      text_revision_id: "rev:1",
      analysis_run_id: "run:1",
      created_at: "2026-01-01T00:00:00Z",
    },
    characters: [
      { id: "char:ada", name: "Ada Mbeki", provenance: "observed" },
      { id: "char:tomas", name: "Tomas Reiner", provenance: "asserted" },
      { id: "char:yeong", name: "Sister Yeong", provenance: "observed" },
      { id: "char:bram", name: "Bram", provenance: "observed" },
    ],
    relations: [
      // declared, never enacted
      aRelation({
        id: "rel:ada--tomas@asserted",
        source: "char:ada",
        target: "char:tomas",
        provenance: "asserted",
        weight_basis: "asserted_statements",
        types: ["estrangement", "kinship"],
      }),
      // enacted, never declared
      aRelation({
        id: "rel:ada--yeong",
        source: "char:ada",
        target: "char:yeong",
        weight: 40,
      }),
      // both
      aRelation({
        id: "rel:ada--bram@asserted",
        source: "char:ada",
        target: "char:bram",
        provenance: "asserted",
        weight_basis: "asserted_statements",
        types: ["alliance"],
      }),
      aRelation({ id: "rel:ada--bram", source: "char:ada", target: "char:bram", weight: 3 }),
    ],
    ...over,
  } as SnapshotDocument;
}

describe("comparing what is declared against what is enacted", () => {
  it("surfaces a relationship in the plan and not on the page", () => {
    const { declaredOnly } = compareProvenance(aDocument());

    expect(declaredOnly).toHaveLength(1);
    expect(declaredOnly[0].source).toBe("char:ada");
    expect(declaredOnly[0].target).toBe("char:tomas");
  });

  it("surfaces a relationship on the page and not in the plan", () => {
    const { enactedOnly } = compareProvenance(aDocument());

    expect(enactedOnly).toHaveLength(1);
    expect(enactedOnly[0].target).toBe("char:yeong");
  });

  it("counts a pair both classes cover as agreed, once", () => {
    const { agreed } = compareProvenance(aDocument());

    expect(agreed).toHaveLength(1);
    expect(agreed[0].asserted).not.toBeNull();
    expect(agreed[0].observed).not.toBeNull();
  });

  it("matches pairs by endpoints rather than by relation id", () => {
    // 4.3 made the identifiers differ by provenance on purpose. Keying on id would find no
    // agreement anywhere and report the whole corpus as both undeclared and unenacted.
    const document = aDocument();
    const ids = document.relations.map((relation) => relation.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(compareProvenance(document).agreed).toHaveLength(1);
  });

  it("matches a pair whichever way round each side named it", () => {
    const document = aDocument({
      relations: [
        aRelation({ id: "a", source: "char:ada", target: "char:bram", provenance: "asserted" }),
        aRelation({ id: "b", source: "char:bram", target: "char:ada" }),
      ],
    });

    expect(compareProvenance(document).agreed).toHaveLength(1);
  });

  it("puts what was declared and never enacted first", () => {
    // The finding an author came here for should not be below a page of agreements.
    const order = compareProvenance(aDocument()).pairs.map((pair) => pair.agreement);

    expect(order).toEqual(["declared-only", "enacted-only", "agreed"]);
  });

  it("carries what the reference material called the relationship", () => {
    const [declared] = compareProvenance(aDocument()).declaredOnly;

    expect(declared.types).toEqual(["estrangement", "kinship"]);
  });

  it("leaves an enacted-only pair untyped rather than inventing a type", () => {
    const [enacted] = compareProvenance(aDocument()).enactedOnly;

    expect(enacted.types).toEqual([]);
  });
});

describe("what the comparison refuses to say", () => {
  it("never compares the two weights", () => {
    // An observed weight counts passages of contact and an asserted weight counts
    // statements. "Declared more strongly than enacted" is not a sentence this data
    // supports, so a pair covered by both is simply agreed, whatever the numbers say.
    const document = aDocument({
      relations: [
        aRelation({
          id: "a",
          source: "char:ada",
          target: "char:bram",
          provenance: "asserted",
          weight_basis: "asserted_statements",
          weight: 1,
        }),
        aRelation({ id: "b", source: "char:ada", target: "char:bram", weight: 400 }),
      ],
    });
    const [pair] = compareProvenance(document).agreed;

    expect(pair.agreement).toBe("agreed");
    expect(Object.keys(pair)).not.toContain("delta");
  });

  it("is withheld from a corpus with no reference material", () => {
    // Every relation would be "enacted but never declared", which restates that the corpus
    // has no bible rather than finding anything in it.
    const document = aDocument({
      relations: [aRelation({ id: "a", source: "char:ada", target: "char:yeong" })],
    });
    const comparison = compareProvenance(document);

    expect(comparison.available).toBe(false);
    expect(comparison.enactedOnly).toEqual([]);
  });

  it("says which half is missing rather than only that it is unavailable", () => {
    const document = aDocument({
      relations: [aRelation({ id: "a", source: "char:ada", target: "char:yeong" })],
    });

    expect(compareProvenance(document).unavailable).toContain("no reference material");
  });

  it("is withheld from a corpus that is all reference material", () => {
    const document = aDocument({
      relations: [
        aRelation({
          id: "a",
          source: "char:ada",
          target: "char:tomas",
          provenance: "asserted",
          weight_basis: "asserted_statements",
        }),
      ],
    });
    const comparison = compareProvenance(document);

    expect(comparison.available).toBe(false);
    expect(comparison.unavailable).toContain("nothing in it has been enacted");
  });

  it("is withheld from a snapshot with no relations at all", () => {
    const comparison = compareProvenance(aDocument({ relations: [] }));

    expect(comparison.available).toBe(false);
    expect(comparison.pairs).toEqual([]);
  });
});

describe("relations that are neither", () => {
  it("counts a hand-entered relation instead of dropping it", () => {
    // Invariant 5 has three provenances. A human relation is not evidence of the corpus
    // disagreeing with itself, but omitting it silently would make these totals disagree
    // with every other total in the application.
    const document = aDocument();
    document.relations.push(
      aRelation({ id: "rel:hand", source: "char:bram", target: "char:yeong", provenance: "human" }),
    );
    const comparison = compareProvenance(document);

    expect(comparison.other).toHaveLength(1);
    expect(comparison.pairs.map((pair) => pair.key)).not.toContain("char:bram char:yeong");
  });

  it("does not let a hand-entered relation make a pair look agreed", () => {
    const document = aDocument({
      relations: [
        aRelation({
          id: "a",
          source: "char:ada",
          target: "char:tomas",
          provenance: "asserted",
          weight_basis: "asserted_statements",
        }),
        aRelation({ id: "b", source: "char:ada", target: "char:yeong" }),
        aRelation({ id: "c", source: "char:ada", target: "char:tomas", provenance: "human" }),
      ],
    });
    const comparison = compareProvenance(document);

    expect(comparison.declaredOnly).toHaveLength(1);
    expect(comparison.agreed).toEqual([]);
  });
});

describe("marking the graph", () => {
  it("marks both edges of an agreed pair", () => {
    const marks = marksFor(compareProvenance(aDocument()));

    expect(marks.get("rel:ada--bram")).toBe("agreed");
    expect(marks.get("rel:ada--bram@asserted")).toBe("agreed");
  });

  it("marks the declaring edge of a pair nothing enacted", () => {
    const marks = marksFor(compareProvenance(aDocument()));

    expect(marks.get("rel:ada--tomas@asserted")).toBe("declared-only");
  });

  it("marks every relation it compared and nothing else", () => {
    const document = aDocument();
    document.relations.push(
      aRelation({ id: "rel:hand", source: "char:bram", target: "char:yeong", provenance: "human" }),
    );
    const marks = marksFor(compareProvenance(document));

    expect(marks.size).toBe(4);
    expect(marks.has("rel:hand")).toBe(false);
  });
});

describe("the comparison as sentences", () => {
  it("names both findings and the agreement", () => {
    expect(describeComparison(compareProvenance(aDocument()))).toBe(
      "1 declared but never enacted, 1 enacted but never declared; 1 agreed.",
    );
  });

  it("says so plainly when the plan and the page agree throughout", () => {
    const document = aDocument({
      relations: [
        aRelation({
          id: "a",
          source: "char:ada",
          target: "char:bram",
          provenance: "asserted",
          weight_basis: "asserted_statements",
        }),
        aRelation({ id: "b", source: "char:ada", target: "char:bram" }),
      ],
    });

    expect(describeComparison(compareProvenance(document))).toContain("both declared and enacted");
  });

  it("gives the reason when the question cannot be asked", () => {
    const document = aDocument({
      relations: [aRelation({ id: "a", source: "char:ada", target: "char:yeong" })],
    });

    expect(describeComparison(compareProvenance(document))).toContain("no reference material");
  });

  it("names characters rather than identifiers", () => {
    const entries = findingList(aDocument(), compareProvenance(aDocument()));

    expect(entries[0].subject).toBe("Ada Mbeki and Tomas Reiner");
  });

  it("shows what was declared beside the pair that was never enacted", () => {
    const entries = findingList(aDocument(), compareProvenance(aDocument()));

    expect(entries[0].detail).toBe("estrangement, kinship");
  });

  it("falls back to the identifier when a character is missing from the snapshot", () => {
    const document = aDocument({ characters: [] });
    const entries = findingList(document, compareProvenance(document));

    expect(entries[0].subject).toContain("char:");
  });

  it("orders the entries as the comparison does", () => {
    const document = aDocument();
    const entries = findingList(document, compareProvenance(document));
    const agreements: Agreement[] = entries.map((entry) => entry.agreement);

    expect(agreements).toEqual(["declared-only", "enacted-only", "agreed"]);
  });
});

describe("marking the built graph", () => {
  function someElements(): GraphElement[] {
    return [
      { data: { id: "char:ada" } },
      { data: { id: "rel:ada--tomas@asserted", source: "char:ada", target: "char:tomas" } },
      { data: { id: "rel:ada--yeong", source: "char:ada", target: "char:yeong" } },
      { data: { id: "rel:hand", source: "char:bram", target: "char:yeong" } },
    ];
  }

  it("puts the agreement on the edge that carries it", () => {
    // 3.4 shipped its overlay with the marks computed where nothing could test them, and
    // they were silently never applied. This is that test, for this overlay.
    const elements = applyMarks(someElements(), compareProvenance(aDocument()));

    expect(elements[1].classes).toBe("declared-only");
    expect(elements[2].classes).toBe("enacted-only");
  });

  it("leaves characters alone", () => {
    const elements = applyMarks(someElements(), compareProvenance(aDocument()));

    expect(elements[0].classes).toBeUndefined();
  });

  it("leaves a relation the comparison did not cover alone", () => {
    const elements = applyMarks(someElements(), compareProvenance(aDocument()));

    expect(elements[3].classes).toBeUndefined();
  });

  it("keeps any class an element already had", () => {
    const elements: GraphElement[] = [
      {
        data: { id: "rel:ada--yeong", source: "char:ada", target: "char:yeong" },
        classes: "isolated",
      },
    ];
    applyMarks(elements, compareProvenance(aDocument()));

    expect(elements[0].classes).toBe("isolated enacted-only");
  });

  it("marks nothing when the comparison could not be made", () => {
    const document = aDocument({
      relations: [aRelation({ id: "rel:ada--yeong", source: "char:ada", target: "char:yeong" })],
    });
    const elements = applyMarks(someElements(), compareProvenance(document));

    expect(elements.every((element) => element.classes === undefined)).toBe(true);
  });
});

describe("marks and the confidence class", () => {
  it("adds an overlay mark without dropping one already there", () => {
    // 5.5 marks an uncertain edge in `buildGraph`; the overlays mark elements afterwards.
    // Replacing rather than appending would lose whichever ran first, and the stylesheet —
    // not this function — is where the two are ranked.
    const elements = [{ data: { id: "rel:ada--tomas@asserted" }, classes: "uncertain" }];

    const marked = applyMarks(elements, compareProvenance(aDocument()));

    expect(marked[0].classes?.split(" ").sort()).toEqual(["declared-only", "uncertain"]);
  });
});

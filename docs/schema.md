# Schema reference

**Version 0.1.0** ·
[`src/dramatis/schema/dramatis.schema.json`](../src/dramatis/schema/dramatis.schema.json) ·
JSON Schema draft 2020-12 · CC BY 4.0

A Dramatis snapshot document is a character relationship graph bound to the exact text and
the exact analysis that produced it. The schema is versioned and published separately from
the application so other tools can emit and consume Dramatis JSON without running Dramatis.

## Shape

```
snapshot document
├─ schema_version
├─ collection          one character registry, spanning one or more works
├─ works[]             a novel, a series, a season
├─ documents[]         files, each narrative or reference
├─ text_revisions[]    immutable, content-hashed states of the text
├─ analysis_runs[]     model, prompt version, parameters
├─ snapshot            binds one text revision to one analysis run
├─ characters[]        nodes
└─ relations[]         edges
```

## Two time axes, never collapsed

A snapshot binds a `text_revision_id` **and** an `analysis_run_id`. Both are required.

This is the schema's load-bearing decision. When a graph changes between two snapshots, the
user must be able to say whether it changed because the work was rewritten or because the
analysis improved. Collapse the two and every diff becomes uninterpretable — which is also
why a Dramatis result can be cited: the reader can reproduce the exact pairing.

## Medium neutrality

The schema names no unit belonging to any particular form. There is no `chapter`, no
`panel`, no `scene`. Instead:

- A **work** declares `segment_types` — an ordered vocabulary, outermost first.
- A **locator** is a `path` of typed segments drawn from that vocabulary.

A novel might declare `["part", "section", "paragraph"]`; a stage work something else
entirely. The types are data supplied per work, never enum members in the schema. A test in
`tests/test_schema.py` enforces this by scanning every schema document the package ships.

```json
{
  "locator": {
    "document_id": "doc:main",
    "path": [
      { "type": "part", "index": 2, "label": "The Return" },
      { "type": "section", "index": 34 },
      { "type": "paragraph", "index": 12 }
    ]
  }
}
```

## Evidence and re-anchoring

Every piece of evidence carries a `locator` (where) and a `selector` (what was said). The
selector follows the W3C Web Annotation `TextQuoteSelector`: an `exact` quotation with
optional `prefix` and `suffix`.

Offsets are recorded as a hint, never as the authority. Once an author edits a paragraph,
every offset after it shifts, but the quotation and its surrounding context still locate the
passage. This is what lets evidence survive revision — the whole point of snapshots.

The `exact` string must be found verbatim in the source text. Extractions failing that check
are rejected rather than surfaced with a warning.

## Provenance

Every character and relation records where its claim came from:

| Value | Meaning |
|---|---|
| `observed` | The narrative text enacts it. |
| `asserted` | The author stated it in reference material outside the narrative. |
| `human` | A person entered or corrected it in the application. |

Corpora with a character bible or wiki produce both `observed` and `asserted` edges, and the
disagreement between them is informative: a relationship declared but never enacted is a gap
worth knowing about. Corpora without reference material produce only `observed` edges, and
nothing breaks.

`review_status` (`proposed` → `accepted` / `corrected` / `rejected`) is separate, and tracks
how far a claim has travelled through human review.

## Weights

`weight` drives rendered edge thickness. `weight_basis` is **required** alongside it and
names what the weight counts — interactions, shared segments, authored emphasis.

Weights are comparable only within a shared basis, so the basis travels with the weight
through every export. Two graphs built on different bases must not be diffed as though their
numbers meant the same thing.

## Open vocabularies

`relation.types` and `segment.type` are free strings, deliberately. No closed vocabulary of
relationship types survives contact with real narrative, and imposing one would push every
interesting relation into an `"other"` bucket.

## Versioning

The schema follows semantic versioning, independently of the application. Every document
records the `schema_version` it was written against, and Dramatis reads every version it has
ever published.

Each version is identified by a `$id` under the project's own domain, with the version in
the path:

```
https://kestoralabs.co.uk/dramatis/schema/0.1/dramatis.schema.json
```

That string is a stable identifier, not necessarily a live document. Other tools use it to
say which version of the format they emit or consume, so it must not change once published —
a new version gets a new path, and the old one keeps meaning what it always meant.

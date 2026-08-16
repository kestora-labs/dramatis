# Dramatis — Roadmap & Build Specification

*"the persons of the drama"*

Dramatis analyses a narrative body of work and produces a graph of its characters and
their relationships. Nodes are characters. Edges are relationships, weighted by closeness
and decorated with notes and pointers back into the source. Every analysis is saved as an
immutable snapshot, so the graph can be compared across drafts and revisions to show how
the relationships evolved as the work was written.

A Kestora Labs product. Open source. Local-first. Authors first, scholars close behind.

---

## How to use this document

This file is both a human roadmap and a build prompt. An agent asked to advance the
project should:

1. Read **Working rules**. They govern how every change is made.
2. Read **Invariants**. They are not negotiable and are not re-litigated per phase.
3. Read **Reference corpora**. Every design decision is tested against these four shapes.
4. Find the lowest-numbered phase whose **Acceptance** criteria are not all met.
5. Within that phase, find the lowest-numbered unchecked bullet and implement *only* that
   bullet. Do not pull work forward from later bullets or later phases, even when it looks
   cheap. Phases are ordered so that each one is usable on its own.
6. Tick the bullet and open a short note in `DECISIONS.md` for any choice that contradicts
   or extends this document.

If a requirement here conflicts with something discovered during implementation, stop and
say so rather than quietly reinterpreting it.

---

## Working rules

These apply to every change, in every phase, without exception.

**1. The work lives in a git repository.** One repository per Kestora Labs product;
Dramatis is its own. Nothing is built outside version control.

**2. One bullet, one commit.** Each numbered bullet in each phase is a single, complete,
self-contained commit. Do not batch several bullets into one commit, and do not split a
bullet across commits unless it proves genuinely too large — in which case say so, and
propose splitting the bullet in this document first.

**3. Commit messages are prefixed with the bullet number.** The message begins with
`phase {phase}.{bullet}`, followed by an em dash and an imperative summary:

```
phase 1.1 — ingest a single plain-text file with content hashing
phase 1.2 — segment text into an ordered path of typed segments
phase 2.4 — re-anchor evidence quotes after source edits
```

Commits made by an agent carry the standard `Co-Authored-By` trailer.

**4. Every commit includes its tests, and they pass before it is made.** No commit lands
red. Tests are written in the same commit as the code they cover, not deferred to a
later cleanup pass.

Where a bullet produces no executable code — a licence file, a documentation page — the
equivalent gate applies instead, and the commit states which: schema validation for schema
changes, link and build checks for documentation, CI configuration proven by a passing
run. A bullet is never exempt from having *some* automated check that it did what it
claimed.

**5. The full suite passes, not just the new tests.** A commit that fixes its own tests
while breaking an earlier phase's is not done.

---

## Non-goals

State these plainly so they don't creep in:

- **Not a writing tool.** Dramatis does not edit the work. It reads and reflects.
- **Not tied to one author's method.** See Invariant 2. Personal conventions — filename
  version suffixes, status tags, stage vocabularies, section layouts — live in optional
  adapters or in inferred-and-confirmed ingest mappings, never in the core schema.
- **Not a general knowledge graph.** Characters and their relationships. Places, objects,
  and events are out of scope until a phase says otherwise.
- **Not a model.** Dramatis orchestrates a model the user brings. It ships no keys and no
  weights.
- **Not multi-tenant.** No accounts, no sharing, no hosted manuscript custody in v1.

---

## Invariants

1. **The schema is medium-neutral.** No `chapter`, `panel`, `beat`, `episode`, or `scene`
   as schema keys. Structural position is an ordered path of typed segments, where the
   segment *types* are data supplied per work, not enum members baked into the schema.
2. **Every feature must serve at least two reference corpora.** A feature useful only to
   one shape is an adapter or a plugin, not core.
3. **Evidence verifies or it does not ship.** Every quotation attached to a node or edge
   must be found verbatim in the source text by a programmatic check. Extractions failing
   this check are rejected, not surfaced with a warning.
4. **Snapshots are immutable, and the two time axes are separate.** A snapshot binds a
   *text revision* to an *analysis run*. Never collapse them: the user must always be able
   to tell whether the graph changed because the work changed or because the analysis did.
5. **Everything carries provenance.** Each node and edge records whether it was `observed`
   (enacted in the narrative), `asserted` (stated by the author in reference material), or
   `human` (entered or corrected in the app). Views may filter by provenance.
6. **Reading data never requires a model.** The app must open, render, diff, and export
   any stored snapshot with no network access and no API key. Models are needed only to
   *produce* new analyses.
7. **No egress except to the user's chosen provider.** No telemetry, no analytics, no
   phone-home. This is a headline feature for people holding unpublished manuscripts.
8. **The schema is a separately versioned, published artifact.** Other tools should be
   able to emit and consume Dramatis JSON without running Dramatis. It follows semver and
   ships with its own JSON Schema document.

---

## Reference corpora

Design against all four. They are the test matrix.

| # | Shape | Characteristics | Canonical example |
|---|---|---|---|
| **A** | Single-file work | One text, no revisions, no reference material. Observed relations only. | *Pride and Prejudice* (Project Gutenberg) |
| **B** | Multi-file draft | Folder of chapter files, real revision history, perhaps a cast list. | A novel-in-progress; Scrivener or plain folder |
| **C** | Reference + serial corpus | Character bible or wiki alongside episodic narrative at mixed stages of completion. Both asserted and observed relations. | Series bible + episode outlines + scripts |
| **D** | Scholarly corpus | Published text, several editions, third-party critical annotations, citation requirements. | An annotated edition with external commentary |

Fixtures for each live in `fixtures/`. **A** is public-domain and committed. **B**, **C**,
and **D** are synthetic or anonymised — never commit anyone's unpublished work.

---

## Stack

Chosen in Phase 0, revisable only with a `DECISIONS.md` entry.

- **Backend** — Python, FastAPI. Chosen for the research ecosystem, not for speed.
- **Storage** — SQLite, single portable file. Postgres as an option from Phase 4.
- **Frontend** — TypeScript, Vite, React. **Cytoscape.js** for rendering, **graphology**
  for metrics.
- **Model access** — a provider-agnostic adapter. BYO key. Anthropic first; local models
  via Ollama from Phase 4.
- **Serving** — `dramatis serve` on `localhost:7373`.

---

## Phases

### Phase 0 — Foundations

*No application. Establish the contract everything else depends on.*

- [x] **0.1** — Initialise the git repository and language scaffold: `pyproject.toml`,
      `package.json`, formatter and linter config, test runners wired up and proven by one
      trivial passing test on each side.
- [x] **0.2** — CI running tests and linting on push, proven green.
- [x] **0.3** — `LICENSE` (Apache-2.0 for code), `docs/` under CC BY 4.0,
      `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` stating these working rules, `DECISIONS.md`.
- [x] **0.4** — `schema/dramatis.schema.json` v0.1: collection, work, document, text
      revision, analysis run, snapshot, character, relation, evidence, locator, selector,
      provenance, review status.
- [x] **0.5** — A validator: `dramatis validate <file.json>` passes or fails a document
      against the schema, with useful error messages.
- [x] **0.6** — Fixture **A** committed, comprising: the source text with its provenance
      recorded; a small hand-authored snapshot document whose every evidence quotation is
      verbatim from that text; and an **expectation floor** — principal characters that
      must be present as single nodes with aliases merged, relations that must exist, and
      pairs that must *not* be joined. Plus deliberately malformed documents for the
      negative case.
- [x] **0.7** — Hand-authored skeleton fixtures for **B**, **C**, **D** — structure only,
      no analysis.

> **On the expectation floor.** An earlier draft of 0.6 asked for a hand-verified expected
> graph of the whole work. That is not achievable honestly: the canonical example has some
> sixty named characters and several hundred defensible relationships, and a fixture nobody
> genuinely verified is worse than none, because it launders a guess into a reference. A
> floor states only what was actually checked. Exact-match baselines are generated and
> frozen later, once the pipeline is trusted enough to be worth freezing against.

**Acceptance:** `dramatis validate fixtures/a/snapshot.json` exits 0. Every deliberately
malformed document under `fixtures/a/invalid/` exits non-zero with a message naming the
problem. Every evidence quotation in the fixture is found verbatim in the fixture's source
text. No medium-specific vocabulary appears anywhere in the schema (grep for `chapter`,
`panel`, `beat`, `episode`).

---

### Phase 1 — Walking skeleton

*Text in, graph on screen. Corpus shape **A** only.*

- [x] **1.1** — Ingest a single plain-text file; store it with a content hash as a text
      revision.
- [x] **1.2** — Segment into an ordered path of typed segments, with types supplied by the
      caller and defaulting to a flat `section` when unknown.
- [x] **1.3** — Provider-agnostic model adapter, BYO key, with a recorded, replayable
      request log so tests never depend on a live API.
- [x] **1.4** — Map-reduce extraction over segments: characters, aliases, pairwise
      interactions, each with a verbatim supporting quotation.
- [x] **1.5** — Alias resolution into a per-collection character registry with stable IDs.
- [x] **1.6** — Aggregate to relations with a `weight` and a declared `weight_basis`.
- [x] **1.7** — Verbatim verification gate (Invariant 3) rejecting failed extractions.
- [x] **1.8** — Persist as a snapshot bound to `(text_revision, analysis_run)`, recording
      model ID, prompt version, and parameters.
- [x] **1.9** — `dramatis serve` renders the graph: edge width on a sqrt scale, node size
      by degree.

**Acceptance:** Ingesting fixture **A** end to end produces a snapshot that validates
against the schema. Elizabeth Bennet and Fitzwilliam Darcy are present as single nodes
with their aliases merged, joined by an edge among the graph's heaviest. Every stored
quotation is found verbatim in the source. The whole run is reproducible from the
recorded run metadata.

---

### Phase 2 — Evidence and inspection

*Make the graph answerable. "Why is this edge here?"*

- [ ] **2.1** — Node and edge detail panels: aliases, relation types, weight basis,
      confidence.
- [ ] **2.2** — Evidence list per edge — quotation, note, locator — ordered by position in
      the work.
- [ ] **2.3** — Click a piece of evidence to open the source text at that position,
      highlighted.
- [ ] **2.4** — Quote re-anchoring by exact match, then prefix/suffix, then fuzzy, so
      evidence survives edits to the underlying text.
- [ ] **2.5** — Filters: minimum weight, relation type, provenance.
- [ ] **2.6** — Layout controls and a pinnable layout so a graph looks the same on
      reopening.

**Acceptance:** From any edge in fixture **A** a user reaches the exact supporting passage
in two clicks. Editing the source text — inserting a paragraph before a quoted passage —
leaves the evidence correctly anchored after re-ingest.

---

### Phase 3 — Snapshots and evolution

*The reason the project exists. Corpus shape **B**.*

- [ ] **3.1** — Multi-file ingest for shape **B**, with per-file revision tracking.
- [ ] **3.2** — Snapshot list per work, with text-revision and analysis-run lineage shown
      separately.
- [ ] **3.3** — Diff two snapshots: added, removed, strengthened, weakened, retyped edges;
      added, removed, merged, split characters.
- [ ] **3.4** — Diff rendered both as a graph overlay and as a readable change list.
- [ ] **3.5** — Absolute vs. relative edge-width scaling, toggleable, defaulting to
      absolute so the picture does not appear to change when only totals move.
- [ ] **3.6** — Re-run an analysis against a new text revision while holding the prompt
      constant, and against a new prompt while holding the text constant.

**Acceptance:** Given two revisions of fixture **B** differing by one rewritten chapter,
the diff attributes every change to the text revision and reports no spurious changes
elsewhere. Re-running the identical analysis on identical text produces an identical
graph.

---

### Phase 4 — Heterogeneous corpora

*Reference material alongside narrative. Corpus shape **C**. Self-hosting.*

- [ ] **4.1** — Folder ingest producing a proposed **structure map**: for each document,
      is this narrative or reference material, what is its addressing scheme, does it
      appear to be a revision of another document.
- [ ] **4.2** — The structure map is proposed by the model and confirmed or corrected by
      the user, then saved and reused on subsequent ingests. No convention is hardcoded.
- [ ] **4.3** — Extraction of `asserted` relations from reference documents, distinct in
      provenance from `observed` relations extracted from narrative.
- [ ] **4.4** — Overlay view comparing asserted against observed, surfacing relations
      declared but never enacted, and enacted but never declared.
- [ ] **4.5** — Character registry scoped to a collection so characters may span multiple
      works.
- [ ] **4.6** — Ollama provider adapter for fully local analysis.
- [ ] **4.7** — Docker image; Postgres as an alternative store.

**Acceptance:** Fixture **C** ingests without any code specific to its filing conventions.
The structure map is editable and persists. A relation asserted in reference material and
absent from the narrative is surfaced as such. A full analysis completes against a local
model with the machine offline.

---

### Phase 5 — Curation and continuity

*The graph becomes correctable, and starts reporting problems.*

- [ ] **5.1** — Review status per node and edge: `proposed`, `accepted`, `corrected`,
      `rejected`.
- [ ] **5.2** — Human corrections persist across re-analysis and are never silently
      overwritten.
- [ ] **5.3** — Manual merge and split of characters, with the decision recorded in the
      registry.
- [ ] **5.4** — Continuity report: entities renamed between revisions with references to
      the old name still present elsewhere; references to structural positions that no
      longer exist; documents superseded but still referenced.
- [ ] **5.5** — Confidence surfaced in the UI — low-confidence edges visually distinct.

**Acceptance:** A correction made in snapshot *n* survives re-analysis into snapshot *n+1*
and is reported as `human` provenance. Renaming an entity across one document of fixture
**C** while leaving stale references in another produces a continuity report naming every
stale location.

---

### Phase 6 — Release, interoperability, scholarship

*Corpus shape **D**. Make it citable, exportable, and installable by non-technical users.*

- [ ] **6.1** — Exports: GraphML, GEXF, CSV node/edge lists, JSON-LD.
- [ ] **6.2** — Evidence exportable as W3C Web Annotation with `TextQuoteSelector`.
- [ ] **6.3** — Import of externally produced Dramatis JSON — the schema proves
      interoperable.
- [ ] **6.4** — Multiple editions of one work in a single collection (shape **D**).
- [ ] **6.5** — `CITATION.cff`, Zenodo DOI on release, versioned schema documentation, and
      **the schema served as a static document at its own `$id`** —
      `https://kestoralabs.co.uk/dramatis/schema/<version>/dramatis.schema.json` — so the
      identifier resolves to an authoritative copy and the format can be implemented
      without cloning the repository. Every published version stays served, permanently;
      a new version takes a new path and never replaces an existing one.
- [ ] **6.6** — Documentation site: install, first analysis, schema reference, prompt
      customisation.
- [ ] **6.7** — Tauri desktop wrapper with signed installers for macOS and Windows.

**Acceptance:** A Dramatis graph opens correctly in Gephi via GEXF. A snapshot exported
and re-imported is byte-identical after normalisation. A non-technical user installs the
desktop build and completes a first analysis without touching a terminal.

---

## Backlog

Not scheduled. Do not build without promotion to a phase.

- **Epistemic layer** — who knows what about whom, and as of when. Valuable for concealed
  identity plots; needs its own design pass.
- **Narrative-time scrubber** — the graph assembling as the work progresses, using the
  locators already on every edge.
- **Community detection and centrality** as first-class views.
- **Connectors** — Google Drive, Scrivener, Obsidian, Markdown vaults, TEI.
- **BookNLP interop** — ingest its output as an alternative extractor.
- **Collaborative annotation** for editorial teams and seminar use.

---

## Release and distribution

**Licensing.** Apache-2.0 for code — the patent grant and institutional familiarity matter
more here than copyleft. Documentation and the schema specification under CC BY 4.0 so the
format can be implemented freely by others.

**Channels**, in the order they arrive:

1. `pipx install dramatis` → `dramatis serve`. Developers and early adopters, from Phase 1.
2. Docker image. Self-hosting individuals and institutions, from Phase 4.
3. Signed desktop installers. Non-technical authors, from Phase 6.

All three are the same core. The desktop build is a shell around the local server, not a
fork.

**Versioning.** Application and schema version independently, both semver. A snapshot
records the schema version it was written against, and Dramatis reads every earlier
schema version it has ever published.

**Citability.** `CITATION.cff` in the repository, a Zenodo DOI minted per GitHub release,
and the schema specification archived alongside. A scholar citing a Dramatis result cites
an application version, a schema version, a model ID, and a prompt version — all four are
recorded in every snapshot by Invariant 4.

**Model costs.** Users bring their own API key, or run locally. Dramatis ships no
credentials and brokers no billing.

**Privacy posture**, stated prominently in the README: nothing leaves the machine except
the text sent to the provider the user configured, and with a local model nothing leaves
at all. No telemetry, ever. This is the difference between a tool a novelist will point at
an unpublished manuscript and one they won't.

**Governance.** Solo-maintained at the outset, and honest about it. Issues open, PRs
welcome, no promise of response time. Revisit if a real contributor base appears.

---

## Glossary

- **Collection** — a set of related works sharing a character registry. A standalone novel
  is a collection of one.
- **Work** — a single narrative body: a novel, a series, a season.
- **Document** — one file in a corpus. May be narrative or reference material.
- **Text revision** — an immutable, content-hashed state of a work's text.
- **Analysis run** — one execution of the extraction pipeline, with model, prompt version,
  and parameters recorded.
- **Snapshot** — a graph produced by binding one analysis run to one text revision.
- **Locator** — an ordered path of typed segments identifying a position within a work.
- **Selector** — a quotation with surrounding context, used to re-anchor evidence after
  the text has been edited.
- **Provenance** — `observed`, `asserted`, or `human`.
- **Weight basis** — the declared meaning of an edge's weight; weights are comparable only
  within a shared basis.

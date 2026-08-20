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
7. **No egress except to the user's chosen provider, and to a source the user has named as
   holding their corpus.** No telemetry, no analytics, no phone-home. This is a headline
   feature for people holding unpublished manuscripts. A named source is read-only, is
   contacted only while ingesting, and is never contacted unless a person named it in that
   run — so a corpus already kept in somebody's cloud drive can be read from where it lives
   without the manuscript being exposed anywhere it was not already. Reading a *stored*
   project stays offline regardless, which is Invariant 6 and is not weakened by this.
   *(Amended before phase 6; see **D56**.)*
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
- [x] **0.4** — `src/dramatis/schema/dramatis.schema.json` v0.1: collection, work, document,
      text revision, analysis run, snapshot, character, relation, evidence, locator, selector,
      provenance, review status. *(Written at `schema/` in the repository root; moved into the
      package by 1.14, which is where 6.5 publishes it from. See **D20**.)*
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
- [x] **1.10** — Make the project file findable and knowable. Four parts: a `status`
      command reporting the resolved store path, its works, revisions, snapshots and
      registry size; **no silent creation on read paths** — `analyse` and `serve` pointed
      at a non-existent store say so rather than conjuring an empty one; project discovery
      that walks up from the working directory, as `git` finds `.git`; and a recorded
      decision on whether a store holds one work or one collection, with the CLI made to
      reflect it.

> **Why 1.10 exists.** The store defaults to `dramatis.sqlite` relative to the working
> directory. Run a command from the wrong folder and nothing fails — a second, empty
> project is created and the command reports success. For the intended user, who keeps
> several properties in separate folders, that is a quiet way to end up with two
> half-populated stores that both look plausible. Phase 1 proved the pipeline; this makes
> the thing the pipeline writes to something a person can locate and identify.
>
> The fourth part is a decision, not a feature. The character registry is scoped to a
> **collection**, so a shared universe spanning several works wants one store holding all
> of them. The code already allows it; nothing in the CLI suggests it, and a default
> filename sitting in the current directory actively implies one project per folder.

- [x] **1.11** — Write down what a project is: one corpus studied over time, holding
      settings as well as data. Glossary entry, and a `settings` accessor over the existing
      `meta` table. See **D17**.
- [x] **1.12** — Move the extraction prompt to `src/dramatis/prompts/extract.md`, and record
      a hash of the prompt text actually sent in every run. `require_comparable()` refuses
      two snapshots whose prompt hashes differ, whatever their versions claim. See **D18**.
- [x] **1.13** — A project-level setting for whether a collective counts as an actor, asked
      on the ingest that creates a project, carried into each run's parameters and into
      comparability. Correct the prompt's treatment of indefinite referents at the same
      time, which is not governed by the setting. See **D19**.

- [x] **1.14** — Ship the schema inside the package: `src/dramatis/schema/dramatis.schema.json`,
      read through `importlib.resources`, and asked for by the tests the way an installed
      copy asks for it. Publication in 6.5 reads it from there. See **D20**.

> **Why 1.14 exists.** `dramatis validate` is bullet 0.5, and it worked for nobody who had
> not cloned the repository: the schema sat outside the package, so the wheel did not
> contain it. Everything about that was invisible from here, because every test read the
> file by a path only a checkout has. The bullet is as much about how the schema is tested
> as about where it lives — a resource that is only ever reached the way a developer
> reaches it is a resource nobody has checked is installed.

> **Why 1.11–1.13 exist.** Phase 1 was complete and its acceptance met. Then the first run
> against a live model — the first time any of this met prose it had not been written
> against — showed three things at once: that the prompt is the part most in need of
> revision and the worst placed for it, that an editable prompt quietly voids the
> comparability `PROMPT_VERSION` promises, and that whether a collective is a character is
> a property of the corpus rather than a fact about narrative. None was visible from the
> scripted tests, because a scripted response cannot disagree with the prompt that produced
> it. The phase reopens rather than deferring to Phase 2: every snapshot made before 1.12
> is one whose prompt cannot be recovered.

- [x] **1.15** — Checkpoint a run's model calls, so an interrupted analysis resumes instead
      of paying for the work again. Opt-in `--checkpoint`, keyed by the request fingerprint
      that already exists, written after every call rather than at the end. See **D21**.

> **Why 1.15 exists.** The first run against the whole novel made sixty-three successful
> extraction calls and then raised in the stage after them, and every one of those results
> was discarded — they live in a list in memory until `save_snapshot` at the very end, so
> the pipeline has no state between "nothing" and "a finished snapshot". The cause of that
> particular failure is 1.16's business; this bullet is about the loss being total and
> independent of the cause. Any error in any later stage destroys the same work, and the
> longer the corpus the more there is to destroy — which makes this the bullet that has to
> land first, because without it every attempt at the next one costs another full run.

- [x] **1.16** — Size resolution's token budget from the number of names being grouped rather
      than fixing it, and report a reply that ran out of budget as truncated rather than as
      malformed JSON. See **D22**.

> **Why 1.16 exists.** Resolution is a single call that must name every surface form it was
> given, so the length of its reply is set by the size of the cast — where every other model
> call in the pipeline is bounded by a window. A constant is therefore not merely too small
> at some size, it is the wrong shape: 4096 fit a three-chapter excerpt of twenty-three
> names and could not fit a novel, and no larger constant would be right either. The second
> half is what made the first half hard to see. A budget that runs out under constrained
> decoding yields a valid *prefix*, so the failure arrived as "expected JSON but got
> `{"groups":[{...`" — which reads as a model emitting nonsense and sends the reader to the
> prompt rather than to the budget.

- [x] **1.17** — Disambiguate aliases *after* grouping rather than before, so a form claimed
      by several surface variants of one character is kept, while a form claimed by several
      genuinely different characters is still dropped. The Jane/Elizabeth case is the
      constraint, not an afterthought: it must keep failing closed. See **D23**.

> **Why 1.17 exists.** The first full-novel run resolved cleanly and then showed the
> alias guard failing in both directions at once.
>
> **Over-dropping.** `_resolve_aliases` runs before grouping, so its notion of "claimed by
> more than one character" is really "claimed by more than one *surface form*" — and
> deciding which surface forms are one character is exactly what the step after it does. So
> `lizzy`, seen thirty-six times, was dropped as ambiguous because it was claimed by
> "Elizabeth", "Elizabeth Bennet", and "Eliza Bennet"; `mr. darcy`, `charles`, and
> `my aunt philips` went the same way. The mechanism is right and D7's "conflict, not
> vocabulary" reasoning stands — it correctly dropped `my father` (four different fathers),
> `your sister`, and `she`. Only the ordering is wrong.
>
> **The constraint that makes 1.17 non-trivial.** `miss bennet` was claimed by "Elizabeth",
> "Elizabeth Bennet", *and* "Jane" — extraction proposed it as Elizabeth's alias in some
> windows, which is precisely the error `fixtures/a/README.md` calls this fixture's chief
> value. The guard dropped it as contested, and that accident is the only reason the trap
> did not spring. A fix that resolves claimants to characters first must still refuse this
> one, because after grouping the claimants are two *different* characters and the conflict
> is real. Getting 1.17 right means `lizzy` survives and `miss bennet` does not.
>
> **Under-dropping, and why it is not here.** `you` and `madam` are registered aliases of
> Elizabeth Bennet, because only one character ever claimed them and conflict detection has
> nothing to compare; `she` was caught only because two characters happened to claim it.
> That was drafted as 1.18 and has moved to **7.7**. It is not a filter anyone can write
> without first deciding what a name is, and that decision is a prompt question settled by
> measurement rather than a guard settled by argument. See **D24**.

**Acceptance:** Ingesting fixture **A** end to end produces a snapshot that validates
against the schema. Elizabeth Bennet and Fitzwilliam Darcy are present as single nodes
with their aliases merged, joined by an edge among the graph's heaviest. Every stored
quotation is found verbatim in the source. The whole run is reproducible from the
recorded run metadata. A command run against a store that does not exist says so, and
`dramatis status` answers "which project am I in, and what is in it" without opening the
file by hand. Editing the shipped prompt and re-running produces a snapshot that refuses to
be compared with the one before it, naming the prompt as the reason. Every file the
application reads at runtime is reached as a package resource, so a copy installed from a
wheel validates and analyses without a checkout anywhere on the machine.

---

### Phase 2 — Evidence and inspection

*Make the graph answerable. "Why is this edge here?"*

- [x] **2.1** — Node and edge detail panels: aliases, relation types, weight basis,
      confidence.
- [x] **2.2** — Evidence list per edge — quotation, note, locator — ordered by position in
      the work.
- [x] **2.3** — Click a piece of evidence to open the source text at that position,
      highlighted.
- [x] **2.4** — Quote re-anchoring by exact match, then prefix/suffix, then fuzzy, so
      evidence survives edits to the underlying text.
- [x] **2.5** — Filters: minimum weight, relation type, provenance.
- [x] **2.6** — Layout controls and a pinnable layout so a graph looks the same on
      reopening.

**Acceptance:** From any edge in fixture **A** a user reaches the exact supporting passage
in two clicks. Editing the source text — inserting a paragraph before a quoted passage —
leaves the evidence correctly anchored after re-ingest.

> **Second sentence met. First blocked on phase 4 for fixture A, and met for a real run.**
>
> *Re-anchoring after an edit* is done and demonstrated at full scale: a paragraph inserted
> near the front of the novel and re-ingested leaves every sampled quotation anchored to
> exactly the words it recorded, reported as moved, on the exact rung of the ladder. An edit
> *inside* a quotation falls to the fuzzy rung, which says so rather than presenting a guess
> as a citation. See **D28**.
>
> *Two clicks to the passage* holds on any snapshot whose segmentation can be reproduced,
> which the first full-novel run is. Fixture **A** is hand-authored against *chapters*, and
> the work declares `chapter › paragraph` while storing none of the rules that found them —
> so its locators name positions no blank-line division can address, and the server refuses
> rather than opening the third block of the title page and calling it chapter 3. Closing
> this needs the structure map of **4.1**–**4.2**, not another bullet here. See **D27**.

---

### Phase 3 — Snapshots and evolution

*The reason the project exists. Corpus shape **B**.*

- [x] **3.1** — Multi-file ingest for shape **B**, with per-file revision tracking.
- [x] **3.2** — Snapshot list per work, with text-revision and analysis-run lineage shown
      separately.
- [x] **3.3** — Diff two snapshots: added, removed, strengthened, weakened, retyped edges;
      added, removed, merged, split characters.
- [x] **3.4** — Diff rendered both as a graph overlay and as a readable change list.
- [x] **3.5** — Absolute vs. relative edge-width scaling, toggleable, defaulting to
      absolute so the picture does not appear to change when only totals move.
- [x] **3.6** — Re-run an analysis against a new text revision while holding the prompt
      constant, and against a new prompt while holding the text constant. *(Taken out of
      order: it blocked the acceptance for 3.2 and 3.3. See **D35**.)*
- [x] **3.7** — A document's identity is its path *and* the content that was at it, never
      the content alone. Two files holding the same bytes at different places in one corpus
      are two documents — the ordinary state of a drafts folder, not a corner case. See
      **D40**.

> **Why 3.7 exists.** 3.1 made document identifiers content-addressed so that an edited file
> became a new row instead of overwriting the text an older revision points at, and **D32**
> named the path as where a file's identity actually lives. Only the *stem* reached the
> identifier. `dramatis ingest fixtures/b` — one revision of the folder holding both drafts,
> which is a sentence a user is entitled to say — therefore asked one document to sit at two
> positions in one revision, and raised on `revision_documents`' composite key. Fixture **B**
> has two such pairs: the chapters nobody touched between drafts.
>
> The crash is the guard working, and what it was stopping is the part worth recording.
> Without the composite key the single row would have carried whichever path was written
> last, so a quotation from the first draft would have cited the second, and per-file
> tracking would have had no path to report the first draft's chapter under at all. That is
> the same class of failure — a revision quietly describing a text other than its own — that
> 3.1 was opened to fix, arriving by the other door.

**Acceptance:** Given two revisions of fixture **B** differing by one rewritten chapter,
the diff attributes every change to the text revision and reports no spurious changes
elsewhere. Re-running the identical analysis on identical text produces an identical
graph.

> **Met, once 3.6 was taken out of order.** The diff of the two drafts reports exactly
> what `corpus.json` predicts — the Auber/Idris edge weakened, the Neve/Idris and
> Auber/Neve edges strengthened, no character changes — and now attributes all of it to the
> text revision, with no warnings, including when the two analyses ran a month apart. The
> other direction holds too: the same revision under a different setting attributes to the
> analysis. What stood in the way was one field recording an outcome inside identity
> material; see **D35**.

---

### Phase 4 — Heterogeneous corpora

*Reference material alongside narrative. Corpus shape **C**. Self-hosting.*

- [x] **4.1** — Folder ingest producing a proposed **structure map**: for each document,
      is this narrative or reference material, what is its addressing scheme, does it
      appear to be a revision of another document. **A document may be divided into
      regions**, so that front matter, a critical preface, or an appendix bound into the
      same file is classified separately from the narrative it surrounds.
- [x] **4.2** — The structure map is proposed by the model and confirmed or corrected by
      the user, then saved and reused on subsequent ingests. No convention is hardcoded —
      in particular, nothing anywhere defines what a preface *is*. The model proposes where
      the narrative begins and ends, the user corrects it, and the answer is stored as a
      property of that document rather than as a rule about documents in general.
      *(Regions added by **D31**. See **D39**.)*
- [x] **4.3** — Extraction of `asserted` relations from reference documents, distinct in
      provenance from `observed` relations extracted from narrative. *(See **D41**.)*
- [x] **4.4** — Overlay view comparing asserted against observed, surfacing relations
      declared but never enacted, and enacted but never declared. *(See **D42**.)*
- [x] **4.5** — Character registry scoped to a collection so characters may span multiple
      works. *(See **D43**.)*
- [x] **4.6** — Ollama provider adapter for fully local analysis. *(See **D44**.)*
- [x] **4.7** — Docker image: a multi-stage build that ships the API, the built client, and
      every prompt in one container running `dramatis serve` against a mounted store. *(Split
      from Postgres, which became **4.10**; see **D45**.)*
- [x] **4.8** — The server's first **mutating endpoints**, and the guard they need. Writes
      are confined to project metadata — settings, the structure map, and creating a store —
      and every one of them refuses a request whose `Origin` is not the server's own. A
      browser can post across origins to `127.0.0.1` from any page the user has open, and
      the side effect lands even though the reply cannot be read. Decided once here rather
      than retrofitted after **5.1** has added a dozen more. `serve --help` stops claiming
      it only reads. *(**D31**. See **D46**.)*
- [x] **4.9** — **Project creation in the browser**: choose a single file, a folder, or a
      folder tree; name the store; set whether collectives are actors; confirm the regions
      **4.2** proposed, so a critical preface can be excluded from analysis before a token
      is spent on it. Creation ingests and records settings; it never calls a model, which
      remains `analyse`'s job alone. Prompt selection is deliberately absent — it belongs to
      **7.4**–**7.5**, where prompts become versioned artefacts. *(**D31**. Depends on
      **4.11**, which makes an excluded region actually reduce the cast; done first, as
      **3.6** was. See **D48**.)*
- [x] **4.10** — Postgres as an alternative store. The `Store` interface is unchanged; a
      driver behind it speaks either SQLite or Postgres, chosen by the store URL. This is
      larger than it looks: `rowid` tie-breaking (added by **3.2** and **3.4** to keep
      snapshot and revision ordering stable) has no Postgres equivalent and needs an explicit
      monotonic column with a migration, and every `?` placeholder and `PRAGMA` is
      SQLite-specific. Tested against a real Postgres, never a mock. *(Split from **4.7**; see
      **D45** and **D49**.)*
- [x] **4.11** — **Analysis honours an excluded region.** A region a person marks `excluded`
      in the structure map is dropped from the document at ingest, so its characters never
      reach extraction — the mechanism **4.9** confirms in the browser and **D31**'s preface
      finding needs. Mechanical, not a prompt instruction: the text is not sent rather than
      sent with an ask to ignore it, matching **4.3**'s "split before reading" and keeping
      exclusion a configuration rather than a model behaviour. *(Split from **4.9**; see
      **D47**.)*

- [x] **4.12** — **A corpus source is an interface**, and the local filesystem becomes one
      implementation of it. A source answers two questions: what is the stable root this
      corpus is known by, and what are its readable documents as `(path, text)` pairs.
      Everything downstream — hashing, revisions, structure maps, exclusion — already works
      on exactly that, so this is a refactor with no behaviour change and a test that says so.
      *(Depends on nothing. It is what stops the next three bullets touching `ingest`.
      See **D57**.)*
- [x] **4.13** — **A Google Drive source**: walk a folder tree, export each Google Doc to
      Markdown — which keeps the headings that structure inference reads — and download
      native text files as they are. Anything it cannot read is skipped *with its reason*,
      as a folder's non-text files already are. Identity is unchanged: **D32**'s hash is
      taken over the exported text, so an edited Doc becomes a new document and a new
      revision exactly as an edited file does. Tested against recorded traffic, never a
      live Drive.
      *(The committed traffic is written to the API's documented shape rather than captured,
      since the credential flow that would capture it is **4.14**; the recorder and a live
      test are in place, and the file says which it is. See **D58**.)*
- [x] **4.14** — **Authentication, and `dramatis ingest` against a Drive folder.** An OAuth
      installed-app flow: the user brings a client secret, consents in a browser once, and
      the refresh token is cached outside the project file — a project store is a thing
      people send to each other, and a credential must not travel in one. Read-only scope.
      Refused unless the run names a Drive source, so a typo cannot reach the network.
      *(See **D59**.)*
- [x] **4.15** — **Re-ingest over a Drive root**, so revisions work: a second ingest of the
      same folder picks up edited documents as a new text revision, and the structure map
      confirmed against that root is reused rather than asked again. This is what makes
      **3.x**'s diff and **5.4**'s continuity report usable on a corpus nobody ever
      downloads. *(See **D60**.)*

**Acceptance:** Fixture **C** ingests without any code specific to its filing conventions.
The structure map is editable and persists. A relation asserted in reference material and
absent from the narrative is surfaced as such. A full analysis completes against a local
model with the machine offline. A project is created from the browser without touching the
command line, and a preface excluded there produces a cast free of the people it discusses.
A corpus held in a cloud drive is ingested from where it lives, with Google Docs read as text
and nothing downloaded by hand; re-ingesting it produces a second revision the diff runs
across.

*Reopened before phase 6 (**4.12**–**4.15**): every corpus this application had seen until
then was on a local disk, and most of the ones it is wanted for are not. See **D56**.*

---

### Phase 5 — Curation and continuity

*The graph becomes correctable, and starts reporting problems.*

- [x] **5.1** — Review status per node and edge: `proposed`, `accepted`, `corrected`,
      `rejected`. Recorded *beside* the immutable snapshot rather than in it, and keyed to the
      claim rather than to the document, so a decision outlives the reading it was taken in.
      Append-only: the newest ruling stands and the ones it superseded remain readable.
      *(See **D50**.)*
- [x] **5.2** — Human corrections persist across re-analysis and are never silently
      overwritten. A correction replaces one field of one node or edge and is applied when a
      snapshot is *built*, so it survives every later reading; the corrected entry is `human`
      per Invariant 5. Where a later reading proposes something else the correction stands and
      the reading's competing claim is recorded rather than swallowed — the promise cuts both
      ways. *(See **D51**.)*
- [x] **5.3** — Manual merge and split of characters, with the decision recorded in the
      registry. Both are one operation — surface forms moving between characters — so a split
      is the undo of a merge. The record *is* the mechanism: the next reading resolves the
      moved forms to whoever now claims them, so nothing rewrites a snapshot and **3.4**'s
      merge detection needed no change. Reviews and corrections follow a merged character, or
      **5.1** and **5.2** would be undone by it. *(See **D52**.)*
- [x] **5.4** — Continuity report: entities renamed between revisions with references to
      the old name still present elsewhere; references to structural positions that no
      longer exist; documents superseded but still referenced. Three findings a re-analysis
      cannot produce, checked between *documents* — the grain the mistake actually has, and
      what removes the need for a stop-list. It reports and never repairs: each finding has
      more than one right answer. *(See **D53**.)*
- [x] **5.5** — Confidence surfaced in the UI — low-confidence edges visually distinct.
      Dotted below the midpoint of the declared interval, since dashed already means two other
      things; an *absent* confidence is never drawn as a low one. **Nothing in the pipeline
      records confidence**, so on Dramatis's own readings the sidebar says exactly that —
      asking a model for it changes the prompt hash and belongs to **7**. *(See **D54**.)*

**Acceptance:** A correction made in snapshot *n* survives re-analysis into snapshot *n+1*
and is reported as `human` provenance. Renaming an entity across one document of fixture
**C** while leaving stale references in another produces a continuity report naming every
stale location. *Both met — see **D51** and **D53**.*

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
      without cloning the repository. The document published is
      `src/dramatis/schema/dramatis.schema.json`, the same one the application reads
      (**D20**); there is no second copy to keep in step. Every published version stays
      served, permanently; a new version takes a new path and never replaces an existing
      one.
- [ ] **6.6** — Documentation site: install, first analysis, schema reference, prompt
      customisation.
- [ ] **6.7** — Tauri desktop wrapper with signed installers for macOS and Windows.

**Acceptance:** A Dramatis graph opens correctly in Gephi via GEXF. A snapshot exported
and re-imported is byte-identical after normalisation. A non-technical user installs the
desktop build and completes a first analysis without touching a terminal.

---

### Phase 7 — The prompts as the object of study

*Everything before this treats a prompt as an input. From here it is the thing being
revised, and revising it has to be measurable by someone who did not write it.*

- [ ] **7.1** — An evaluation harness: run a fixture through the pipeline and score the
      result against its expectation floor, reporting which checks moved in which direction.
      Non-zero exit on a regression. Nothing later in this phase means anything without it.
- [ ] **7.2** — Frozen regression baselines, generated from a trusted run rather than
      hand-written — the exact-match fixtures **D5** deferred until there was a pipeline
      worth freezing against.
- [ ] **7.3** — Recorded runs published as evaluation inputs, so the harness replays a real
      corpus with no key and no network. A cassette of a full-novel run already exists as a
      by-product of **1.15**; this makes it a fixture rather than a side effect.
- [ ] **7.4** — The resolution prompt out of its module, under the same file-and-hash
      discipline as extraction. *(Promoted from the Backlog. **D18** covers only the
      extraction prompt, so a changed resolution prompt still silently voids comparability.)*
- [ ] **7.5** — Per-project prompt overrides: a project may carry its own prompts, recorded
      in the run's parameters and honoured by `require_comparable()`, so a house style is a
      first-class thing to study rather than a local edit to an installed package.
- [ ] **7.6** — A prompt changelog, and a contribution guide stating what evidence a prompt
      change must carry to be reviewable at all.
- [ ] **7.7** — Settle what counts as a name rather than a way of referring to someone, and
      stop the second kind entering the registry. `you`, `madam`, `my dear aunt`, and
      `my sister` are aliases of real characters today. *(Drafted as 1.18 and deferred here
      by **D24**; depends on 7.1, since the whole question is which rule scores better.)*

> **Why this phase is not like the others.** The rest of the roadmap finishes. This one does
> not, and it is placed last because it depends on nearly all of it — the harness needs
> fixtures **B**, **C**, and **D** to be real (Phases 3–4), and contributors need something
> installable and citable to contribute *to* (Phase 6).
>
> The reason it is a phase at all rather than a habit is that prompt work without
> measurement is taste, and taste does not merge. Every prompt question this project has
> already hit came from a live run and was settled by argument: whether a collective is a
> character (**D19**), whether an indefinite referent is one, and — still open — whether
> `extract.md`'s *"or described in relation to each other"* should count Wickham describing
> Lady Catherine as an interaction between them, which is the sole negative control fixture
> **A** currently fails. Each of those is defensible either way. What decides them is a
> corpus, a floor, and a number that moves.
>
> **1.15 is what makes this affordable.** Because every model call is fingerprinted and
> recorded separately, a change to the resolution prompt replays sixty-three extraction calls
> from disk and costs one live call — so an experiment costs cents rather than the price of a
> whole run. Prompt refinement stops being something only the person paying the bill can do,
> which is the precondition for anyone else joining in.

**Acceptance:** A prompt change that improves one fixture and regresses another is reported
as such before it is merged, by a command anyone can run. A contributor with no API key
reproduces the evaluation from published recordings. Every prompt the pipeline sends is a
file with a version and a hash, and no prompt reaches a model from inside a module.

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

- **Project** — the study of one narrative corpus over time: a single collection, its text
  revisions, its analysis runs, and the snapshots binding them. Holds the settings the study
  is conducted under, not only its data. One project is one file.
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

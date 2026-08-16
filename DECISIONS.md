# Decisions

A running log of choices that contradict, extend, or resolve an ambiguity in
[`AI/ROADMAP.md`](AI/ROADMAP.md). Newest last. Each entry states the decision, the reason,
and what it would take to reverse it.

---

## D1 — One repository per product

**Phase 0.1.** Dramatis is its own repository rather than a directory in a Kestora Labs
monorepo.

Products under the Kestora Labs umbrella have different audiences, release cadences, and
licences. A per-product repository gives Dramatis its own issues, releases, stars, and
Zenodo DOI, and keeps unrelated history out of the tree a scholar might archive.

*Reversible* until the first public release; painful afterwards, since a DOI points at a
repository.

---

## D2 — Python backend, TypeScript client, in one repository

**Phase 0.1.** The roadmap names the stack; this records where things live. Python package
under `src/dramatis/` (src layout, so tests import the installed package rather than the
working tree). Web client under `web/` with its own `package.json` and lockfile.

Not a monorepo tool (no Nx, Turborepo, or workspaces) — two independent toolchains with
two CI jobs is less machinery than the project currently earns.

*Reversible* cheaply.

---

## D3 — Vite and React deferred to Phase 1.9

**Phase 0.1.** The roadmap's stack section names Vite and React, but Phase 0 ships no UI.
Installing them now would mean carrying an unused dependency tree through four bullets, so
`web/` currently holds only TypeScript, Vitest, and Prettier. Vite and React arrive with
the first rendered graph.

*Reversible* trivially.

---

## D4 — CI is proven locally until a remote exists

**Phase 0.2.** Bullet 0.2 asks for CI "proven green". The GitHub organisation is not yet
created, so there is nowhere to push and no run to observe.

What has been proven instead: the workflow parses, declares the expected triggers, and
runs exactly the commands that pass locally on both toolchains — asserted by tests in
`tests/test_ci_workflow.py`. The bullet is complete in substance; the green badge follows
the first push.

**Resolved 2026-08-16.** `kestora-labs/dramatis` was created public and `main` pushed. The
first CI run passed on all four jobs — Python 3.11, 3.12, 3.13, and Web — so the workflow is
now proven by an actual run rather than by local equivalence. The tests in
`tests/test_ci_workflow.py` remain, since they are what stops the workflow drifting away
from the commands contributors run.

---

## D5 — Fixture A asserts an expectation floor, not an exact graph

**Phase 0.6.** The bullet originally asked for "a hand-authored, hand-verified expected
graph" of the fixture work. Amended, before implementation, to a verified floor: characters
that must be present as single nodes with aliases merged, relations that must exist, and
pairs that must not be joined.

*Pride and Prejudice* has roughly sixty named characters and several hundred defensible
relationships. Nobody was going to verify all of them by hand, and a fixture presented as
verified but actually guessed is worse than no fixture, because downstream phases would
treat it as ground truth. A floor states only what was actually checked, and Phase 1's
acceptance was already written in those terms ("among the graph's heaviest"), so this makes
the two consistent.

Exact-match baselines remain worth having, but as regression fixtures generated from a
trusted pipeline and then frozen — not as hand-written ground truth invented before the
pipeline exists.

*Reversible* at any time by tightening the floor as more of the graph gets verified.

---

## D6 — The schema `$id` must resolve, and is served from the owned domain

**Phase 0.4, scheduled into 6.5.** The `$id` originally named `kestora.dev`, a domain the
project does not own. Corrected to `kestoralabs.co.uk`, and Phase 6.5 extended to actually
serve the document there.

An `$id` is a public identifier that outlives the file. Other tools use it to declare which
version of the format they emit or consume, and every snapshot document records the version
it was written against. Under a domain the project does not control, that identifier could
be claimed by someone else, and no one could resolve it to an authoritative copy.

Serving it is what turns the identifier into a reference, and it is what makes the CC BY 4.0
licensing of the schema mean anything to someone implementing the format without cloning the
repository — the case Invariant 8 exists for.

**Consequence, permanent:** once a version is published at its path it is served forever and
its content never changes. A revision takes a new path. Documents in the wild record a
version, and a version that quietly changed meaning would make them unreadable.

*Not reversible* once a version has been published and third parties have recorded it.

---

## D7 — Two kinds of test double, and a fingerprint to keep recordings honest

**Phase 1.3.** The suite uses **scripted** fakes for the bulk of provider testing, plus a
small number of **recorded** real exchanges replayed from a cassette, behind a `live`
pytest marker that is deselected by default and never runs in CI.

Neither alone is sufficient. Scripted fakes never go stale but prove nothing about how a
real provider behaves. Recordings prove the response shape but go stale *silently* — a
prompt gets edited, the recording no longer answers the question the code asks, and the
suite keeps passing against a stale answer.

The fix for the second is a **request fingerprint**: a hash over every field that
determines the response — prompt, system, max_tokens, effort, output schema. A replay
whose cassette has no entry for that fingerprint raises rather than falling back, and the
message names which fields differ from the nearest recording. `metadata` is excluded,
since it labels a call for humans and never reaches the provider.

*Reversible* — dropping the recorded layer would leave a working scripted suite.

---

## D8 — ModelRequest carries no sampling parameters

**Phase 1.3.** No `temperature`, `top_p`, or `top_k` field on the request type, and the
Anthropic provider never sends one.

Current frontier models reject all three with a 400. A field that worked only against
older models would be a trap: it would look available, be accepted by the type, and fail
at the provider. Where those knobs were once used to trade determinism against variety,
`effort` and the prompt do that work now.

The consequence worth knowing: a caller wanting output variety cannot get it by raising
temperature. That is a real capability loss, and the replacement is prompt-level — ask
for several distinct options and choose among them.

*Reversible* if a supported provider ever needs them, but they would belong in a
provider-specific extension rather than the core request type.

---

## D9 — Verification rejects per interaction, and refuses the run past a threshold

**Phase 1.7.** Invariant 3 says extractions failing the verbatim check are rejected rather
than warned about, but does not say what "an extraction" is. Both readings are bad on their
own: rejecting the whole run for one invented quotation discards sixty windows of correct
work, while rejecting only the offending interaction lets a badly misbehaving run produce a
thin graph that looks plausible and leaves no trace of what went missing.

So: rejection is per interaction, plus a circuit breaker. Above a quarter of quotations
unverifiable — over a minimum sample, since a rate computed from two is noise — the whole
extraction is refused with the numbers in the message. The threshold is a smoke alarm, not
a quality target: accurate quoting fails a few percent at most, usually on typography.

*Reversible* — the threshold is a parameter, and setting it to 1.0 restores pure
per-interaction rejection.

---

## D10 — A wrong locator is not an invented quotation

**Phase 1.7.** Verification separates two failures that look alike. A quotation that is not
in the work is an invention and is rejected. A quotation that *is* in the work but carries
the wrong passage is sound evidence with a bad address: it is relocated and counted, not
rejected.

The first implementation also rejected quotations spanning a paragraph break, on the
grounds that no single passage contains them. Running it against fixture A disproved that —
two of its hand-verified quotations run from narration into the speech that answers it, and
both are verbatim in the work. Refusing them would have been the gate lying about what it
checks. They are now attributed to the passage where the span begins.

Verification therefore searches the leaf passages joined, rather than the raw text, so a
match yields the passage directly and a quotation is checked against exactly the material a
locator can name. A quotation inside the work but outside every addressable passage — front
matter, say — is rejected with its own reason, since no honest locator exists for it.

*Not reversible* in spirit: rejecting real evidence for a locator fault is the failure this
entry exists to prevent.

---

## D11 — A snapshot is stored as its rendered document

**Phase 1.8.** Snapshots are kept as the schema-shaped JSON document, in one column, rather
than normalised across tables. The surrounding columns (work, revision, run, hash, created
date) exist for lookup and are never a second source of truth.

The artifact on disk is then exactly the artifact exported and cited, so the archived and
published forms cannot drift apart, and reading one back needs no model, no network, and no
re-rendering step that could differ from the one that produced it (Invariant 6). Normalised
tables would duplicate the schema in a second dialect and give two places for a definition
to live.

The cost is that querying inside a snapshot means loading it. At the size of a character
graph — hundreds of nodes at most — that is not a real cost, and Phase 3's diffing loads
both documents anyway.

*Reversible* by adding derived tables alongside, if querying ever needs them. The document
stays authoritative.

---

## D12 — Two executions of one configuration are two runs

**Phase 1.8.** An analysis run's identifier is derived from everything that determines the
analysis *including when it started*, so running the same configuration twice produces two
runs and two snapshots.

The alternative — deriving it from configuration alone — was tempting because it would make
"identical analysis on identical text yields an identical snapshot" true by construction.
It is wrong: models are not deterministic, so two executions of one configuration routinely
produce different graphs, and collapsing them would leave a single identifier naming two
different results. Under Invariant 4 that is precisely the failure the second axis exists to
prevent.

Writing the test for this surfaced a related property worth stating: **a re-analysis over a
populated registry is not the same analysis.** Resolution consults the model only for names
it does not already know, so the second run does less work and records different parameters.
The graph is the same; the run is not. Both facts belong in the record, and the test now
asserts both rather than pretending the runs are interchangeable.

*Reversible* — but only alongside a decision about what a snapshot identifier promises.

---

## D13 — The API serves the stored document, and binds to loopback

**Phase 1.9.** `GET /api/snapshots/{id}` returns the archived document unchanged: no view
model, no reshaping, no computed fields. The client does its own layout maths from the same
document a reader would cite. This is D11's reasoning one layer out — a second
representation of the same graph is a second place for the truth to live, and the two
drift. A test asserts the served bytes equal the stored ones.

The server binds `127.0.0.1` by default and warns when told otherwise. A project file holds
unpublished work; serving it on every interface by default would put a manuscript on the
office network because someone typed a command.

`fastapi` and `uvicorn` are an optional extra, loaded separately: building the application
needs only the framework, while listening on a port needs the server. Splitting them keeps
tests free of a dependency that only a running process uses, and keeps Invariant 6 true —
validating and analysing a project works with neither installed.

*Reversible* cheaply.

---

## D14 — Square-root scaling for edge width and node size

**Phase 1.9.** Both encodings map onto their range through a square root rather than
linearly.

Interaction counts in a novel are heavily skewed: two leads share dozens of passages while
most pairs share one or two. Linear width renders the leads as ropes and collapses everyone
else into indistinguishable hairlines, hiding exactly the mid-weight structure a reader is
looking for. Under a square root a weight of 1 against a maximum of 100 occupies a tenth of
the range instead of a hundredth.

Node size uses the same curve so the two encodings read consistently — one compressing while
the other did not would make a graph harder to read, not easier.

Absolute versus relative scaling across snapshots is a separate question and belongs to
phase 3.5, where comparing two graphs makes it matter.

*Reversible* — the scale is one function with tests pinning its endpoints.

---

## D15 — A project holds one collection

**Phase 1.10.** A store may contain exactly one collection. Ingesting a work without naming
one joins whatever collection the project already holds; naming a *different* one is
refused, with an error saying to use a separate file.

The character registry is scoped to a collection. A project holding two collections would
hold two casts that cannot see each other, which is not a project — it is two projects
sharing a filename. Worse, the mistake is silent: you would notice when a character who
appears in both properties turned out to be two characters, long after the analyses that
made them.

The rule is what makes a shared universe work. Two novels ingested into one project share
one registry, so a character appearing in both is one character — the case that motivated
scoping the registry above the work in the first place.

**This supersedes part of phase 1.1.** That bullet let an explicit collection name move a
work between collections within one store. There is no longer anywhere to move it to, and
the test now asserts the refusal instead. Renaming a collection is consequently not possible
either; that is a gap, and belongs with the curation work in phase 5 rather than here.

*Reversible* in code, but not in data: a project that had been allowed to accumulate two
casts could not be cleanly separated afterwards.

---

## D16 — Projects are located, and only `ingest` may create one

**Phase 1.10.** Commands search for `dramatis.sqlite` in the working directory and every
directory above it, the way `git` finds `.git`. A path given with `--store` is taken as
given and never searched for. Every command that reads requires the project to exist;
`ingest` alone may bring one into being.

Before this the default was a bare relative path, so a command run one directory over did
not fail — it created a second, empty project and reported success. For someone keeping
several properties in separate folders that is a quiet way to end up with two
half-populated stores that both look plausible, and nothing in the output would have told
them.

The two halves address different failures. Discovery means a command works from anywhere
inside a project instead of only at its root. Refusing to create on read paths means a
command never reports success for work it did not do.

An explicitly named path is deliberately exempt from discovery: being sent somewhere
unexpected because a file happened to sit in a parent directory would be worse than the
problem discovery solves.

*Reversible* cheaply.

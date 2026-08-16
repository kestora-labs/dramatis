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

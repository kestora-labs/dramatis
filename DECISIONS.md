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

**Outstanding:** confirm a green run on `kestora-labs/dramatis` once the organisation
exists.

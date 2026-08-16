# Contributing to Dramatis

Thanks for looking. Dramatis is solo-maintained and pre-alpha; issues and pull requests
are welcome, but there is no promise of a response time.

Before proposing anything substantial, read [`AI/ROADMAP.md`](AI/ROADMAP.md). It is the
build specification, and it is unusually prescriptive on purpose.

## Working rules

These are not style preferences. They are how the project is built, and they apply to
every change including maintainer commits.

**1. The work lives in a git repository.** One repository per Kestora Labs product.

**2. One bullet, one commit.** Each numbered bullet in each roadmap phase is a single,
complete, self-contained commit. Do not batch several bullets into one commit, and do not
split a bullet across commits unless it proves genuinely too large — in which case propose
splitting the bullet in the roadmap first.

**3. Commit messages are prefixed with the bullet number**, followed by an em dash and an
imperative summary:

```
phase 1.1 — ingest a single plain-text file with content hashing
phase 1.2 — segment text into an ordered path of typed segments
phase 2.4 — re-anchor evidence quotes after source edits
```

Commits made by an AI agent carry the standard `Co-Authored-By` trailer.

**4. Every commit includes its tests, and they pass before it is made.** No commit lands
red. Tests are written in the same commit as the code they cover.

Where a change produces no executable code — a licence file, a documentation page — an
equivalent automated gate applies instead, and the commit states which. No change is
exempt from having *some* check that it did what it claimed.

**5. The full suite passes, not just the new tests.**

## Invariants

The roadmap lists eight invariants that no change may violate. Two are worth repeating
here because they are the ones most often broken by well-meaning patches:

- **The schema is medium-neutral.** No `chapter`, `panel`, `beat`, `episode`, or `scene`
  as schema keys. Structural position is an ordered path of typed segments whose types are
  supplied per work.
- **No egress except to the user's chosen model provider.** No telemetry, no analytics, no
  phone-home. A pull request adding any of these will be declined regardless of merit.

## Local setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
npm ci --prefix web
```

Run everything CI runs:

```bash
.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m pytest
npm --prefix web run typecheck && npm --prefix web run format:check && npm --prefix web test
```

On Windows the interpreter is at `.venv\Scripts\python.exe`.

## Licensing of contributions

By submitting a contribution you agree it is licensed under Apache-2.0 for code, or
CC BY 4.0 for documentation and the schema specification, matching the file you are
changing.

## Test corpora

Never commit anyone's unpublished work. Fixtures are public domain, synthetic, or
anonymised. See the reference corpora table in the roadmap.

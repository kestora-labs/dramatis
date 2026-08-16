# Fixture B — multi-file draft

Reference corpus **B**: a folder of chapter files, with real revision history and a cast
list. The shape a novelist working in Scrivener or a plain folder actually has.

Skeleton only — no analysis. Phase 3 develops the snapshot-and-diff work against it.

## Layout

```
b/
├─ corpus.json          structure only: works, documents, revisions
├─ cast.md              a reference document — asserted relations live here
├─ draft-1/             three chapters
└─ draft-2/             the same three chapters, one substantially rewritten
```

Revisions are directories. That is one convention among many and is not privileged
anywhere in the code: `corpus.json` states which files belong to which revision, and
Phase 4's structure inference has to arrive at the same answer without being told.

## What this fixture is for

The diff. `draft-2` changes exactly one thing of consequence: in chapter 3, Auber no longer
confronts Idris directly — the confrontation is relayed through Neve. A correct pipeline
should show the Auber–Idris edge weakening and the Auber–Neve and Neve–Idris edges
strengthening, and should attribute all of it to the text revision rather than to the
analysis.

Everything else is held constant on purpose. A diff that reports changes elsewhere is
reporting noise.

## Content

Synthetic, written for this repository, and deliberately short. It is not trying to be
good; it is trying to have an unambiguous relationship structure that a human can verify
by reading it in five minutes.

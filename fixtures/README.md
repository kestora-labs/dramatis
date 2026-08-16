# Fixtures

The four reference corpus shapes from [`AI/ROADMAP.md`](../AI/ROADMAP.md). They are the test
matrix: every feature must serve at least two of them, and a feature useful to only one is an
adapter, not core.

| | Shape | State | Developed against by |
|---|---|---|---|
| **[a](a/)** | Single-file work | Complete — source, hand-authored graph, expectation floor, invalid cases | Phases 0–2 |
| **[b](b/)** | Multi-file draft with revisions | Skeleton — structure only | Phase 3 |
| **[c](c/)** | Reference material beside serial narrative | Skeleton — structure only | Phase 4 |
| **[d](d/)** | Multiple editions with critical apparatus | Skeleton — structure only | Phase 6 |

"Skeleton" means the corpus exists on disk with its structure described in `corpus.json`, and
no analysis has been run. The `corpus.json` files are plain metadata, deliberately *not*
Dramatis snapshot documents — there is no graph in them to be mistaken for ground truth.

## The conventions differ on purpose

Each fixture files itself differently: **b** puts revisions in directories, **c** puts them in
YAML frontmatter, **d** organises by edition. None of these conventions may be hardcoded
anywhere in the core. Phase 4's structure inference has to propose the right map for each and
have the user confirm it.

If you find yourself special-casing a fixture's layout, that is the "not tied to one author's
method" non-goal being broken.

## Nothing here is anyone's real work

Fixture **a** is public domain. Fixtures **b**, **c**, and **d** are synthetic, written for
this repository. Never commit unpublished work belonging to anyone.

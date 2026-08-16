# Fixture C — reference material alongside serial narrative

Reference corpus **C**: a character bible beside episodic narrative at mixed stages of
completion. Both `asserted` and `observed` relations exist, and they disagree.

Skeleton only — no analysis. Phase 4 develops structure inference and the asserted/observed
overlay against it.

## Layout

```
c/
├─ corpus.json
├─ series-bible/     reference documents — asserted relations
└─ transmissions/    narrative documents — observed relations
```

## The conventions here are deliberately awkward

This fixture uses filing conventions chosen to be *unlike* anything the code might be
tempted to special-case:

- Revisions live in YAML frontmatter (`revision: 3`), not in filenames.
- Status vocabulary is `settled` / `provisional` / `open` — not any other project's words.
- Narrative units are called *transmissions*, not chapters or episodes, and are numbered
  `t01`, `t02`, `t03`.
- One transmission is an outline; two are drafted. Stages are mixed on purpose.

If any of this leaks into the core as a special case, Invariant 1 or the "not tied to one
author's method" non-goal has been broken. The structure map must be *inferred and
confirmed*, never parsed from a hardcoded convention.

## What this fixture is for

The disagreement between what the bible declares and what the narrative enacts.

**Declared but never enacted.** The bible states that Ada Mbeki and Tomas Reiner are
estranged siblings — a relationship given a whole section. They never share a scene in any
transmission. This is the gap an author most wants surfaced: a relationship that exists in
the plan and not on the page.

**Enacted but never declared.** Ada and Sister Yeong carry the most page time of any pair in
the corpus, and the bible does not mention the relationship at all.

A pipeline that merges the two provenance classes into one graph loses both findings. That
is the whole point of the fixture.

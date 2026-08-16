# Fixture A — single-file work

Reference corpus **A** from the roadmap: one text, no revisions, no reference material,
observed relations only. The degenerate case, and the regression test that the schema has
not over-fitted to richer corpora.

## Contents

| Path | What it is |
|---|---|
| `source/pride-and-prejudice.txt` | The work. Public domain. |
| `snapshot.json` | A small hand-authored graph. Valid against the schema; every quotation verbatim from the source. |
| `expectations.json` | The floor a pipeline's output must clear. Not a ground truth — see below. |
| `invalid/` | Deliberately malformed documents for the negative case. |
| `fetch_source.py` | Regenerates the source text from upstream, reproducibly. |

## Provenance of the source text

- **Work:** *Pride and Prejudice*, Jane Austen, first published 1813.
- **Edition:** the R. Brimley Johnson text as issued by George Allen, 1894.
- **Upstream:** `https://www.gutenberg.org/cache/epub/1342/pg1342.txt`, retrieved 2026-08-16.
- **Upstream sha256:** `74f2665d6e6925fc2c17dec644bec9e87df478a0f1836822125e8acbb3777806`
- **Committed sha256:** `e3bb81d19b34dd917187e2836340b02dceb3dc751e18308092a0074bbb2118ab`

The novel is in the public domain worldwide. What is committed here is the work alone: the
distributor's front matter, licence boilerplate, and trademark notice have been stripped
rather than redistributed, and their trademark is not used. `fetch_source.py` performs that
derivation deterministically and `--check` verifies the committed file still matches, so
the provenance is reproducible rather than merely asserted.

If upstream reissues the edition, the hash check will warn. Do not accept a new text
without re-checking the expectation floor against it.

## Why a floor rather than an expected graph

`expectations.json` states only what was actually verified by hand: characters that must be
present, aliases that must resolve to one node, relations that must exist, and pairs that
must *not* be joined.

It deliberately does not claim to be the complete graph. The work has roughly sixty named
characters and several hundred defensible relationships; a file asserting all of them would
be asserting a guess, and every later phase would treat that guess as ground truth. A
pipeline that clears this floor has not been shown correct — only that it has not failed in
one of the ways that matter. Rationale in `DECISIONS.md`, entry D5.

The two negative controls are the useful part. `William Collins` and `George Wickham` share
no scene; neither do `Lady Catherine de Bourgh` and `George Wickham`. An edge between either
pair means proximity within some window is being read as interaction.

## The alias trap

Unqualified **"Miss Bennet"** denotes the eldest unmarried daughter — Jane — not Elizabeth.
A pipeline that folds it into Elizabeth produces a graph that looks entirely reasonable and
is wrong throughout. That single assertion is most of this fixture's value.

## Quotation matching

Quotations are stored whitespace-normalised, and matching normalises the source the same
way, because the source is hard-wrapped and quoted sentences span line breaks that belong to
the layout rather than the work. Nothing else is relaxed: no case folding, no punctuation
substitution. See `src/dramatis/text.py`.

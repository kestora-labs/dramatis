# Findings

Things learned by *using* Dramatis, rather than by building it.

Two lists. **Defects** are confirmed faults with a known cause; they will be fixed, but they
are not scheduled and none of them is promoted to a phase until somebody says so. **Wishes**
are things the tool could plausibly do and currently cannot; most of them will never be built,
and that is the point of keeping them here rather than in the roadmap.

This file has no authority. [`AI/ROADMAP.md`](ROADMAP.md) says what is being built and
[`DECISIONS.md`](../DECISIONS.md) says why it was built that way; both bind. Nothing here does.
A wish that earns its way in becomes a roadmap bullet or a Backlog entry; a defect that gets
fixed is deleted from here and explained there.

Each entry says how it was found, because a fault found by a real corpus is worth more than one
imagined at a desk, and the difference should survive into whoever reads this next.

---

## Defects

### F1 — A document skipped for one reason blocks later ones with a different, false reason

`drive.py` marks a path as taken *before* attempting to read the document at it:

```python
seen.add(entry.path)          # drive.py:234
try:
    documents.append((entry.path, self._read_one(entry)))
except IngestError as error:
    skipped.append((entry.path, str(error)))
```

So when the first document at a path fails to be read, the path is still claimed, and every
later document there is reported as colliding with something that is not there.

**Seen on a real folder.** Three files share the name `Susie_Swell_Victory_Maid_Visual_Canon.html`
in one Drive folder. All three are correctly skipped — they are HTML — but only the first says
so:

```
skipped ...html: not a text file (.html)
skipped ...html: a second document is already at this path
skipped ...html: a second document is already at this path
```

The second and third sentences are untrue. A person acting on them would go looking for a
duplicate they do not have, and would not learn that the real reason is the file type.

**The fix is one line**: claim the path when a document is actually read, not when it is
reached. That also gives the better behaviour in the case nobody has hit yet — where the first
document at a path fails and a second one there could have been read, the path is free and the
second should take it.

### F2 — The duplicate-path message names the loser, and is useless precisely when it fires

```python
skipped.append((entry.path, f"a second document is already at this path ({entry.name!r})"))
```

`entry` is the document being *skipped*, so the message names it rather than the one holding
the path. In the only situation this message can occur — two documents resolving to one path —
the two names are identical by construction, so the parenthetical says nothing at all:

```
skipped Pictures/Susie Swell/Susie_Swell_Visual_Canon.md:
    a second document is already at this path ('Susie_Swell_Visual_Canon')
```

It should name the **winner**, and by its Drive identifier rather than its name, because the
identifier is the only thing that distinguishes the two. A person needs to know which of their
two documents was read in order to decide whether the right one was.

Found alongside **F1**, on the same folder: two different Google Docs both named
`Susie_Swell_Visual_Canon`, in one folder, with different content.

### F3 — The Drive traffic fixture is written rather than captured, and now partly outrun by reality

**D58**'s open caveat: `tests/traffic/*.json` is written to Google's documented Drive v3 shape
rather than recorded from an account, so the suite proves the code does what the documentation
says the API does, and not that the API does it.

A real folder was read on 2026-08-20, and **the API behaved as the fixture assumes** on every
path that run exercised: `files.get` for the root, `files.list` with `q`/`parents` and
pagination across a 20-folder tree of 130 entries, `files/{id}/export?mimeType=text/markdown`,
`files/{id}?alt=media`, and the mime-and-suffix rules that decide what is read. That is most of
the module, and it is no longer guesswork.

A second, larger corpus the same day added a 38-document tree, a document of 1.1MB, a curly
apostrophe in a filename (`Take 2/Gioconda’s war.md`, handled correctly), and the fact that
**export is byte-stable**: reading one Doc twice gives identical content hashes, which is what
**D32**'s identity and **4.15**'s revision chain both rest on.

**What those runs did not exercise, and remains unverified:** the `403 exportSizeLimitExceeded`
branch, `401` on an expired credential mid-walk, `429` rate limiting, shortcuts, and Google
native types other than Docs (no Sheet, Slide or Form was present in either folder). Those four
error branches are the ones written most speculatively and tested least.

Not a defect in behaviour — a gap in evidence, recorded here so it is not mistaken for one.
Re-recording with `pytest -m live -k RealDrive` needs a folder whose contents can be published,
since an exported Doc lands in the recording.

### F4 — A Google Doc's Markdown export inlines its images as base64, and they become "text"

**D56** chose Markdown export because it keeps the headings structure inference reads. Nobody
asked what else it keeps. It keeps the images, as data URIs:

```
[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAggAAAFbCAIAAAA/bOHw...
```

**Measured on a real corpus.** `Pictures/Unmade_Weapons_Wunderfrau_Costume_Reference_v1.md`
exports to **1,099,064 characters**, of which a few hundred are prose and one single line is
393,762 characters of base64 PNG. That one document is **65% of the entire 1.7M-character
corpus**. Forty-two lines, and the file dwarfs thirty-seven other documents combined.

Four consequences, in the order they bite:

- **Cost.** Roughly a quarter of a million tokens of image data would be sent to a model for
  no possible return. On this corpus that is most of the bill.
- **Segmentation.** A 393,762-character "paragraph" is one blank-line section (**D27**), so a
  single segment is larger than any window the pipeline reads in.
- **Evidence.** Invariant 3 verifies quotations against the stored text. Base64 in that text is
  not wrong, exactly, but it is a large region of a document that can never carry a quotation
  and can never be read.
- **The character count means something different.** Every number a person uses to decide
  whether a corpus is worth analysing is inflated by however many images it contains.

**Not a hashing problem, checked rather than assumed.** Exporting the same Doc twice produced
identical content hashes, base64 included, so **D32**'s document identity holds and a re-ingest
does not report an unedited image-bearing document as `changed`. That was the obvious second
failure and it is not there.

**Shape of a fix.** Strip data-URI image definitions from the exported Markdown at read time,
the way an excluded region is dropped at ingest (**4.11**): the stored text is then what every
locator and quotation resolves against, and stays self-consistent. What needs deciding is
whether a stripped image leaves a marker behind — a document that says "an image was here" is
more honest than one that silently omits it, and the structure map may one day want to know.

---

## Wishes

Ordered by how sharply each was felt, not by how likely it is to be built.

### W1 — A way to say "this document is not part of the work"

A document's role is `narrative` or `reference`, and there is no third answer. Only *regions*
may be `excluded` (**4.11**), so excluding a whole document means confirming a region that
happens to cover it — which `--set` cannot express, since it takes a role and validates it
against those two.

**Felt immediately.** The first real corpus holds five image-generation prompt files —
`Susie_Swell_Victory_Maid_LoRA_Prompt_List_v5.md` and four `Victory_Maid_*_Prompt.md` — which
describe how characters should *look to an image model*. They are not narrative and they are
not a character bible. Classifying them as reference feeds prompt vocabulary into extraction;
there is currently no way to leave them out short of not pointing Dramatis at the folder.

The cheap version is `--set path=excluded`, storing a whole-document excluded region. The
question worth answering first is whether "not part of the work" is a *role* or a separate
property, because a document can be reference material and still be excluded from a run.

### W2 — Disambiguate colliding paths instead of dropping one

Today the loser of a path collision is skipped and named (**D58**). That is honest but it is
still data loss: two Google Docs with one name means one of them is not analysed, and the
survivor is chosen by identifier order, which is deterministic but arbitrary.

Appending the Drive identifier to the loser — `Susie_Swell_Visual_Canon (1Zr2bT2h).md` — would
keep both, deterministically, and cost nothing but an uglier path on the rarer document. The
argument against is that a path is how a document is tracked between revisions, and a
synthesised one is a path nobody chose.

Needs **F2** fixed first either way, because the current message cannot even tell you which
document you lost. Both real corpora hit this — two Docs named `Susie_Swell_Visual_Canon` in
one, two named `Unmade_Weapons_Issue2_Synopsis_v1` in the other — so it is ordinary rather than
exotic, and losing half a synopsis is worse than losing a duplicate image.

### W3 — Collapse the skipped list

Reading the first real folder printed **110 lines** of `not a text file (.jpg)` before the
20 documents it had actually found. The information is right and the presentation buries the
result underneath it.

Something like `note: skipped 108 files that are not text (.png ×61, .jpg ×45, .html ×2)`, with
the full list behind `--json`, which already carries it.

### W4 — Say what a corpus is before anything is spent on it

`structure` reports each document's size and no total. Deciding whether to analyse a corpus
means knowing how much of it there is, and that currently means adding twenty numbers by hand.
The first real folder came to ~213,000 characters, which is a real amount of money at a hosted
provider and a real amount of time at a local one. The second came to 1,698,937 — of which,
per **F4**, 65% is base64 image data that nobody would knowingly pay to send anywhere.

A total, and — separately, and only if it can be done honestly — some indication of what a run
would cost against the configured provider.

### W5 — Read HTML as text

`.html` is not in `TEXT_SUFFIXES`, so an HTML document is skipped. The first real corpus
contains four, and they appear to be exported character-canon documents — exactly the reference
material Dramatis wants.

Against: HTML must be converted before a quotation can be verified against it (Invariant 3),
and a bad conversion silently breaks every quotation anchored into it. That is the same class
of risk that makes `read_text` refuse to guess an encoding, so this is not a suffix to add
casually.

### W6 — Prune a subtree from the walk

The first real folder keeps 110 images under `Pictures/`, holding five text documents among
them. Listing them costs one request per folder and nothing is exported, so the cost is
attention rather than money — but a corpus with a large asset tree beside it is clearly common,
and this one is a comic-book bible where it is guaranteed.

A user-supplied path to skip is not a hardcoded convention and so does not offend Invariant 1,
which is worth stating because it looks like it might.

### W7 — Say plainly when a corpus has no narrative in it

The first real folder is entirely reference material: profiles, rogues galleries, a timeline,
cross-references. Dramatis will read it and produce `asserted` relations, and **4.4**'s overlay
— declared but never enacted, enacted but never declared — will have nothing to compare them
against, because there is no observed half.

Nothing is wrong; the result is simply half of what the tool is for, and a person should be
told that before they pay for it rather than after they read the graph.

### W8 — A `diff` command on the command line

`diff_snapshots` exists and the browser uses it; the CLI has no `diff`. Every other thing a
person can do to a project has a command. Minor, and listed mostly so the asymmetry is written
down somewhere.

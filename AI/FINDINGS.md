# Findings

Things learned by *using* Dramatis, rather than by building it.

Two lists. **Defects** are confirmed faults with a known cause; they will be fixed, but they
are not scheduled and none of them is promoted to a phase until somebody says so. One marked
*fixed* is kept only where the measurement behind it is worth more than the fault was. **Wishes**
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
seen.add(entry.path)  # drive.py:234
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

### F4 — A Google Doc's Markdown export inlines its images as base64 — *fixed*

Exported Docs carried their images as `data:` URIs, and one real costume-reference document
came to 1,099,064 characters of which some 2,300 were prose. Fixed by replacing the payload
with a note that says what was removed; the body's `![][image1]` marker survives, so the
document still says an image was there. See **D61** for the reasoning, including why an
uploaded file is left exactly as it is and why the byte count is kept.

Kept here rather than deleted because the measurement is the useful part: **65% of a real
corpus** was three PNGs, and the next person to widen what Dramatis reads should know that a
format's incidental payload can dwarf the work.

### F5 — Ollama silently discards the head of an over-long prompt — *fixed*

The Ollama provider set `num_predict` and never `num_ctx`, so every local call ran inside
whatever context window the server defaulted to. **Ollama does not error when a prompt exceeds
that window. It drops the beginning of the prompt and answers from what is left.**

Measured against a real `llama3.2:3b` on 2026-08-20:

```
characters sent  : 46,689     (~11,600 tokens)
prompt_eval_count: 2,050
reply            : "OK"
```

83% of the prompt was discarded, HTTP 200, no warning anywhere. And the part that survives is
the *tail* — so an extraction call would keep its trailing instruction and lose the passage
above it, and the model would be asked to find characters in text it had never seen. It would
answer. The answer would be recorded in a snapshot as a reading of the work.

That is the worst failure shape this project has: not a crash, but a confident answer to a
question nobody asked. It also quietly falsified Phase 4's acceptance — *a full analysis
completes against a local model with the machine offline* — which would have completed, having
read a fraction of the corpus.

**Fixed** by asking for the window every call needs: `context_for` sizes it from the prompt and
system text at a pessimistic three characters per token, with a floor of 8,192 (well clear of
the server default) and a ceiling of 32,768 (context costs memory, and a model asked for more
than the hardware has fails to load at all). A caller may overrule it, because a smaller window
is a real choice on modest hardware — making it deliberately is not the same as having a server
default make it silently.

**Not closed by the fix:** nothing yet *detects* truncation if it happens anyway. Ollama returns
`prompt_eval_count`, so a provider could compare it against what it sent and refuse a reply that
read less than it was given. It is not done here because Ollama also reports a lower count when
it reuses a cached prefix, and a check that cries wolf on every second call is worse than none.
Worth solving properly — see **W9**.

---

## Wishes

Ordered by how sharply each was felt, not by how likely it is to be built.

### W1 — A way to say "this document is not part of the work" — *built*

A document's role was `narrative` or `reference` with no third answer, and the first two real
corpora both held files that were neither: a to-do roadmap, a script format spec, a production
pipeline spec, a style canon, and five sheets of image-generation prompts.

`--set path=excluded` now says so, at the command line and in the browser flow, and ingest
leaves the document out of the revision entirely rather than storing it as reference material
nobody wanted read. The question this entry asked first — whether "not part of the work" is a
role or a separate property — was answered *role*, for **D47**'s reason: what is excluded is
not a third kind of document but the absence of one, so `documents.role` still takes exactly
two values. See **D62**.

**What it deliberately does not do**, and what this entry becomes: a document can be genuine
reference material and still be something to leave out of *one particular run*. That is a
per-run inclusion set — a different feature with a different lifetime — and it is not built.

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

### W9 — Notice when a local model read less than it was sent

**F5** stops Ollama truncating by asking for a big enough window. Nothing checks that the
window was honoured, and the failure it guards against is silent by nature — a plausible answer
to a passage the model never received.

`prompt_eval_count` comes back on every reply and is the raw material. The obstacle is that
Ollama also reports a low count when it reuses a cached prefix between calls, so the naive
check fires constantly on exactly the workload extraction has. Distinguishing the two — perhaps
by tracking the prefix the previous call shared, or by asking for the window and verifying the
server's own reported `num_ctx` rather than the token count — is the actual work.

Worth doing before anybody trusts a local run of a long corpus, because today the guarantee is
"we asked for enough" rather than "it read it all".

### W10 — `analyse` cannot raise the timeout the error message tells you to raise

`OllamaProvider` defaults to a ten-minute per-call timeout and, when it expires, says:

> Ollama at http://127.0.0.1:11434 did not answer within 600s. A local model on modest
> hardware can be slow; raise the timeout or use a smaller model.

There is no way to raise it. `analyse` exposes `--provider`, `--host`, `--model` and
`--effort`; the timeout is a constructor argument no command line reaches. The advice is
sound and unfollowable.

**Felt on a real machine.** Measured throughput on CPU-only inference was ~31 tokens/second of
prompt evaluation, which puts a 12,000-character extraction window at roughly four to nine
minutes — either side of the ten-minute default depending on how much the model writes back.
A run of six windows can therefore fail on the fourth for no reason but arithmetic, and the
only remedy today is to drive `pipeline.analyse` from Python with a provider built by hand.

A `--timeout` flag is the whole fix. It is listed here rather than done because it is not the
defect the session was chasing, and because the more interesting question is whether the
default should scale with the window size instead of being a constant.

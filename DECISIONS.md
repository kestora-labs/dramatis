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

---

## D17 — A project is the study of one corpus over time, and holds settings

**Phase 1.11.** A project is a single narrative corpus studied over time: one collection,
many text revisions, many analysis runs, many snapshots. It is the unit of continuity —
what makes this month's graph and last month's the same enquiry rather than two unrelated
pictures.

D15 and D16 were derived from this without it ever being written down. Both read as
arbitrary restrictions on their own — why may a store hold only one collection, why may a
read not create a file — and read as consequences once the definition is stated. That is
reason enough to state it, but it also settles something new.

A project holds **settings**, not only data. If a project is a study, then how the study is
conducted is a property of the project, and two snapshots within one project should differ
because the text changed or the run changed, not because a question was silently put a
different way. This is what makes a project-level analysis setting coherent rather than an
arbitrary place to hang a flag, and D19 depends on it.

Settings live in the existing `meta` key/value table, which has held only `store_version`
until now.

*Reversible* only in the sense that a definition can be rewritten. D15, D16, and D19 rest
on it.

---

## D18 — The extraction prompt is a file that ships, and every run records the prompt it used

**Phase 1.12.** `SYSTEM_PROMPT` moves out of [`extraction.py`](src/dramatis/extraction.py)
into `src/dramatis/prompts/extract.md`. A prompt is prose, it is the thing most often
revised, and it is what a reviewer of an analysis most needs to read. None of that is
served by a triple-quoted string in the middle of a module.

It goes in the package rather than in `AI/`, because
`[tool.hatch.build.targets.wheel] packages = ["src/dramatis"]` ships only the package: a
prompt in `AI/` would be present in a checkout and absent from an install, so `analyse`
would work here and fail for everyone else. The package directory ships with the wheel and
with a desktop build later, and the file is still plain Markdown that can be opened and
edited. This also separates the **shipped default** from a **per-project override**, which
`AI/` would have conflated — the place a user edits a prompt is their project, not the
installed package.

The harder half. `PROMPT_VERSION` is recorded in every snapshot so two graphs can be
compared, and Invariant 4 exists so a reader can attribute a change to the text or to the
analysis. Once the prompt is an editable file, that string is a *claim*: edit the file and
two snapshots both say `extract-v1` while having been produced by different instructions.
A comparison that silently lies is worse than one not offered.

So every run records a hash of the prompt text actually sent. The version stays the human
label; the hash is the fact. `require_comparable()` refuses two snapshots whose prompt
hashes differ even when their versions match. Editing the prompt remains allowed and
becomes visible, which is the trade worth making — the alternative, forbidding edits, would
protect the guarantee by removing the reason to want it.

*Reversible* cheaply in code. Not retrospectively: snapshots made before the hash existed
carry no record of their prompt, so their comparability with later ones is unknowable
rather than merely unknown. There is one such snapshot, from the first live run.

---

## D19 — Whether a collective is an actor is a project setting

**Phase 1.13.** The first live run made "the Netherfield party", "Mr. Bingley's sisters",
and "Mrs. Long's nieces" into characters. The prompt instructs it to: *"Report a character
for every person, group, or entity."* This is not a defect in the model's reading.

It is not obviously a defect in the prompt either. In some works a collective genuinely is
an actor — a family, a crew, a House, a faction — and corpus **C** is where that bites, a
character having a relationship with a body rather than a person. Excluding groups outright
would make that unrepresentable, and Invariant 1 argues against assuming every corpus is
about individuals.

What is a defect is the two coexisting as peers. Miss Bingley and Mrs Hurst were separately
nodes, so "Mr. Bingley's sisters" double-counted them and produced one edge where the text
supports two. Nothing recorded that one contains the others. That is not a group being
represented; it is a group being confused with a person.

So the corpus decides, and the project records the decision: a setting fixed when a project
is first created, stored in `meta`, copied into each run's `parameters` beside `effort`, and
part of comparability. Two snapshots analysed under different settings are not two readings
of one corpus — they are answers to different questions, and `require_comparable()` should
say so. Changing the setting later is permitted and reports that it breaks comparison with
existing snapshots, rather than being forbidden or allowed in silence.

The question is asked on the first ingest into a new project, because D16 leaves no separate
initialisation step to hang it on.

Two alternatives were rejected. **Dropping collectives** is one clause in the prompt and
costs corpus C. **Extracting them and folding them into their members afterwards** is
attractive because it needs no setting, but it rewrites what the passage said, which
contradicts the `observed` provenance the claim carries.

Separately, and not governed by this setting: "another young man" is not a collective but an
indefinite referent — a phrase standing in for someone unidentified. It is excluded under
every setting, because an unnamed someone is not an identity to hold a relationship. That is
a prompt correction, not a configuration question.

*Reversible* in code. Not in data: snapshots already made under one setting stay as they
are, which is the point of recording it.

---

## D20 — The schema ships inside the package, and is published from there

**Phase 1.14.** `schema/dramatis.schema.json` moves to
`src/dramatis/schema/dramatis.schema.json`, and `schema.py` becomes that directory's
`__init__.py`. [`load_schema()`](src/dramatis/schema/__init__.py) reads it through
`importlib.resources` instead of walking up from `__file__`.

The old path resolved to `<repo>/schema/` in a checkout and to `<site-packages>/../schema`
in an install, where there is nothing. `[tool.hatch.build.targets.wheel] packages =
["src/dramatis"]` ships the package directory and nothing else, so the schema was simply
absent from the wheel, and `dramatis validate` — bullet 0.5's command, and the first one the
README shows — raised `FileNotFoundError` for everyone who installed rather than cloned.
This is D18's fault a second time, and D18's reasoning applies unchanged: a resource outside
the package is not installed.

The wrinkle is that the schema is not only a runtime resource. D6 commits 6.5 to serving
this document at its `$id`, so the repository-root copy had a second job, and the obvious
fix keeps both: leave the canonical file at `schema/` and have hatchling `force-include`
copy it into the wheel. That was rejected on evidence. **`force-include` puts the file in
the wheel but not in an editable install** — verified against a throwaway package before
choosing. CONTRIBUTING and CI both install with `pip install -e`, so the regression test
this entry exists to add could never have run in a checkout, and the loader would have
needed a fallback to the repository-root path — which restores precisely the arrangement
that hid the fault, where the only route anyone exercises locally is the one that is not
shipped. A fix whose test cannot fail is not a fix.

Publication therefore reads from the package. That is the smaller cost: 6.5 copies a file
from a different directory once, where the alternative leaves the same trap set for every
resource added afterwards. It also settles which copy is canonical by leaving only one, in
the spirit of D11 and D13 — a second copy of a document is a second place for it to be
wrong. The address does not affect the licence; the schema remains CC BY 4.0 and `NOTICE`
names where it now lives.

The tests changed with it, and that is half the entry. They read the schema as a resource of
`dramatis.schema`, and one of them asserts the loader reaches nothing outside the package
directory. The previous tests read it by a path built from the repository root, which is why
a suite of two dozen assertions about this file stayed green through four phases of it not
being installed at all.

*Reversible* cheaply in code, though not to `force-include`: that arrangement cannot be
tested from a checkout, which is the whole of the argument against it.

---

## D21 — A run checkpoints to a cassette, and the fingerprint decides what is already done

**Phase 1.15.** `CheckpointProvider` wraps the live provider, serves any call the cassette
already holds, and writes each new exchange to disk as it arrives. `dramatis analyse
--checkpoint <path>` opts into it.

The first run against the whole novel made sixty-three extraction calls, all of them
successful, and lost every one when the stage after them raised. Nothing was wrong with
those calls. They were simply held in a list in memory, because the pipeline has exactly
two states — nothing written, or a finished snapshot — and an error anywhere between them
lands on the first. On a three-chapter excerpt that is invisible. On a novel it is the
difference between a fault costing pennies and costing the whole run, and it gets worse as
the corpus grows.

**No new keying was needed, which is the good part.** D7 built a request fingerprint over
every field that determines a response, to stop a stale recording being served silently.
That is the same question a checkpoint asks — *has this exact work been done?* — so the
machinery transfers whole, and the consequence falls out for free: change a prompt, a
schema, an effort, or a token budget and only the calls that depended on it are missing
from the cassette. Fixing one stage re-runs that stage. D22 raises resolution's budget, and
because `max_tokens` is in the fingerprint, the extraction calls stay valid and the re-run
costs one call rather than sixty-four. Nothing had to be taught that; it is D7 being right.

Three choices worth stating.

**A separate class, not a flag on `RecordingProvider`.** Recording exists for deliberate
re-recording and must always call live and overwrite; checkpointing must look first. One
class doing both, switched by an argument, would eventually serve a re-record the stale
answer it was invoked to replace — the exact failure D7 exists to prevent.

**Saved per call, not at the end.** A checkpoint written when the run finishes tells the
run that did not finish nothing. This means many small writes, so `Cassette.save()` now
writes alongside and renames: an interrupted save costs the call in flight and never the
calls already banked. A checkpoint that can corrupt itself is not a checkpoint.

**Opt-in, and the caller names the file.** A cassette holds every prompt sent, which for a
real project is the manuscript. Invariant 7 is about egress and this is a local file, so it
is not a breach — but a tool that silently drops a plaintext copy of an unpublished novel
beside the project, under a name the author did not choose, is not one that has earned the
privacy posture the README claims. `*.checkpoint.json` is in `.gitignore` for the same
reason.

What this does **not** do is checkpoint the pipeline's own stages. Verification, resolution,
and aggregation all re-run on a resume; they are cheap, deterministic given the same
extraction, and local. Only the model calls are worth persisting, and only they are
expensive to repeat.

*Reversible* cheaply — the flag is opt-in and nothing else in the pipeline knows the
provider is wrapped.

---

## D22 — Resolution's budget is sized from the cast, and a truncated reply says so

**Phase 1.16.** `resolve()` no longer takes a fixed `max_tokens=4096`. It computes one from
the number of surface forms actually being grouped: a base for the envelope and for
thinking, plus a worst-case allowance per form, clamped to a ceiling. `ModelResponse.json()`
refuses a reply whose `stop_reason` is `max_tokens` before it tries to parse it.

Every other model call in the pipeline is bounded by a window, so a constant works. This one
is not. Resolution is a single call whose reply must name every form it was given, so its
length is set by the size of the cast, and a constant is the wrong *shape* rather than
merely the wrong number — 4096 fit a three-chapter excerpt of twenty-three names, could not
fit a novel, and no larger constant would be right for the work after that.

The allowance is deliberately the worst case: every form its own group, which is exactly
what the deterministic baseline produces and what a model that declines to merge anything
would produce. Forms that *do* merge cost far less, adding a string to a group rather than a
group. Sizing for the pessimistic case means the budget is never the reason a cautious
grouping fails, and since `max_tokens` is a ceiling rather than a charge, being generous
costs nothing when it is not used.

**The second half is why the first half was hard to see.** Under constrained decoding a
reply that runs out of budget is not malformed — it is a valid prefix of a valid answer. So
the failure surfaced through the JSON parser as *expected JSON from anthropic/claude-opus-5
but got `{"groups":[{"canonical_name":"Elizabeth Bennet"…`*, which reads as a model emitting
nonsense and sends the reader to the prompt. The one thing that knew the answer was
incomplete was `stop_reason`, and nothing looked at it. Checking it before parsing is a
three-line change that turns the most expensive failure this project has had into a sentence
naming its own remedy. It is checked *before* parsing rather than in the parser's error path
because a truncated reply can still parse by luck, and is no less incomplete for that.

**What this does not do is make the call unbounded.** The ceiling sits below the smallest
output cap among current models, so a request is never built that a provider would refuse
outright. Above that the honest answer is not a bigger reply but **batching the name list**,
which is a real design question — grouping decided in two passes can contradict itself, and
the existing ambiguity guard protects against conflicts within one pass, not across two. That
belongs in its own bullet with the curation work, not here. Until it exists, the ceiling plus
the truncation message is the difference between a corpus that is too large being reported
and being mysterious.

*Reversible* cheaply. The constants are three module-level numbers with tests pinning the
properties rather than the values, and an explicit `max_tokens` still overrides them.

---

## D23 — Alias ambiguity is judged against characters, not against the names that proposed them

**Phase 1.17.** `_resolve_aliases` moves after grouping and takes the assignments map, so
"claimed by more than one character" is decided on resolved identities rather than on
surface forms.

D7's rule was right and its timing was wrong. Before grouping, "Elizabeth", "Elizabeth
Bennet", and "Eliza Bennet" are three claimants; afterwards they are one character. So a
form all three proposed was read as contested when it was in fact unanimous, and `lizzy` —
the most common familiar form for the protagonist, seen thirty-six times — was discarded
*because* everybody agreed on it. `mr. darcy`, `charles`, and `my aunt philips` went the
same way. Deciding which surface forms are one character is precisely what the next step
does, so the question could not be answered where it was being asked.

Nothing about the principle changes. `my father` (four different fathers), `your sister`,
`his sister`, and `she` are still dropped, still by conflict rather than by vocabulary, and
still without a stop-list.

**The constraint is the interesting half.** Unqualified "Miss Bennet" denotes Jane, and
extraction proposed it as *Elizabeth's* alias in some windows — the error
`fixtures/a/README.md` calls this fixture's chief value. Under the old ordering it was
dropped as contested, and that accident was the only thing standing between the graph and
being wrong throughout. It still fails closed, now for a stated reason rather than a lucky
one: its claimants resolve to two different characters, so the conflict is real. The tests
assert both halves together, because a change that satisfied either alone would be worse
than no change.

**Measured on the recorded run rather than argued.** Replaying the full novel through the
new ordering — identical model output, courtesy of D21 — recovers eleven surface forms and
loses none, leaving characters and relations untouched at 102 and 241. Fixture A's alias
groups go from four of six to five of six. That the structure did not move is the point:
this was only ever a defect of identity, not of the graph.

**What it deliberately does not fix.** "Miss Bennet" is still its own node rather than
Jane's alias. Resolving it needs the convention that the eldest unmarried daughter is
"Miss ⟨surname⟩", and resolution is shown a list of names and occurrence counts, never the
text — so the fixture's hardest case is not merely unsolved but structurally out of reach
from where it is being asked. That belongs with the prompt and evaluation work in Phase 7.

**A consequence worth stating.** The old over-firing was incidentally suppressing junk. With
spurious contest removed, relational epithets like `my dear aunt` and `my sister` now attach
where before they were blocked by an accident — five of the eleven recovered forms are of
that kind. `you` and `madam` were already getting through, so this widens 1.18's surface
rather than creating it, and is an argument for doing 1.18 rather than against having done
this.

*Reversible* cheaply: the ordering is one call site and the guard's signature.

---

## D24 — Deciding what counts as a name is deferred to Phase 7, and Phase 1 closes without it

**Phase 1.17 / 7.7.** The bullet drafted as 1.18 — stop a form only one character ever
claimed from becoming an alias on that basis alone — moves to **7.7**. Phase 1 is complete
with it gone.

The defect is real and unchanged: `you` and `madam` are aliases of Elizabeth Bennet, and
D23 added `my dear aunt` and `my sister` to the list by removing the spurious contest that
had been suppressing them. Conflict detection cannot see them, because only one character
ever claimed them, and `she` was caught only by the accident of two characters claiming it.

**Why it cannot be finished here.** Every available fix is a decision about what a name is,
and none of them is a guard:

- Filtering in resolution means a rule for which strings look like names. That is a
  stop-list wearing a hat, and D7 rejected stop-lists for a reason that has not weakened —
  it encodes one language's conventions into the core, and Invariant 1 argues against
  assuming the shape of a corpus.
- Fixing it in `extract.md` means telling the model not to offer possessives and vocatives
  as aliases. That is the likeliest right answer, and it is a prompt change.
- Fixing it in the resolution prompt is the same kind of change one layer along.

Two of the three are prompt edits, and the third is the one D7 already argued against. A
prompt edit cannot be justified by argument: `my sister` said by Darcy really does denote
Georgiana, so a rule excluding it is trading a true alias for a class of false ones, and
whether that trade is worth making is a number, not an opinion. Phase 7 exists to produce
that number, and 7.7 depends on 7.1 for exactly this reason. Landing a guess in Phase 1
would mean choosing between three defensible options with no way to tell which won, and
then treating the guess as settled.

**What this costs.** Phase 1's acceptance names Elizabeth and Darcy as single nodes with
their aliases merged, and after D23 they are. Fixture A's floor is stricter than that
acceptance and two of its checks still fail: `Miss Bennet` is its own node rather than
Jane's alias, and the Lady Catherine / Wickham negative control has an edge. Both are the
same shape as 7.7 — the first needs the convention that the eldest unmarried daughter is
"Miss ⟨surname⟩", which resolution never sees the text to learn; the second is
`extract.md`'s *"or described in relation to each other"* doing what it says. Neither is a
defect in code that Phase 1 could close, and D5 already warned that this floor states more
than any single phase promises.

So Phase 1 closes on its own acceptance, with three known gaps recorded and homed rather
than silently carried. The alternative — holding the phase open until the fixture floor is
clean — would keep it open until the prompts are finished, which is Phase 7, which is the
phase that does not finish.

*Reversible* — promoting 7.7 back into Phase 1 is a bullet move, though doing so would
re-open a phase whose acceptance is met in order to work on something its acceptance does
not mention.

---

## D25 — A detail panel reports what the snapshot says, and stays silent where it says nothing

**Phase 2.1.** The node and edge panels render only the fields the selected element
actually carries. An absent `confidence`, `salience`, `valence`, `types` or `notes` produces
no row at all, rather than a row reading `—`, `0`, or `none`.

The roadmap names four things the panel must show, and on the corpus the project has, two of
them are usually missing. Fixture **A** is hand-authored and complete: every relation has
types, valence, confidence and a review status. The first full-novel run has none of them —
241 relations, every one carrying only the six required fields. Both are valid documents,
because the schema makes those properties optional.

So the panel had to choose what missing means, and the two readings are not close. A row
reading `Confidence —` says the run considered the question and declined to answer. An
omitted row says the run was never asked. The second is true and the first is a claim the
document does not make, which is the same failure as showing a weight without its basis:
a display that looks like information and is not. Rendering `0` would be worse still, since
zero confidence is a value the schema permits and a real extraction could legitimately
report.

**Degree is the exception, and it is not one.** A character with no relations gets
`Relations 0` rather than nothing, because that zero is not an absence in the document —
it is computed from the relations that are there, and the graph already states it visibly
by drawing the node isolated. The panel is not free to omit something the picture asserts.

**What this costs** is that the panel's shape varies between snapshots, and a user comparing
a fixture against a real run sees a shorter list without being told why. That is the correct
discomfort: the run really did produce less, and a uniform panel would hide a gap between
what the prompts are asked for and what they return. Phase 7 is where that gap becomes a
number.

*Reversible* cheaply — the omission is one guard in `push`, and the field list is a single
array per kind.

---

## D26 — The evidence list sorts what it is given, and ties keep the order they arrived in

**Phase 2.2.** The edge panel orders supporting passages by document, then by structural
path outermost segment first, then by the selector's offset hint. Where two passages cannot
be separated, the comparison returns a tie and a stable sort leaves them as the snapshot
stored them.

**The sort currently changes nothing, and is required anyway.** All 1,022 pieces of evidence
in the first full-novel run are already in position order, because extraction is a
map-reduce over segments taken in order. That is a property of this pipeline rather than of
the format. The schema imposes no ordering on an `evidence` array; aggregation may merge a
relation from windows processed separately; and **2.4** re-anchors quotations against an
edited text, which is exactly an operation that moves them. A view that inherited its order
from the producer would be correct today and quietly wrong the first time any of those
changed, with no test able to say when.

**What it refuses to do is more of the decision than what it does.** Three kinds of passage
cannot be placed: one whose segment carries no `index`, one naming no document in a work
that has several, and two sitting in the same segment with no offsets. Each could be given
a confident position — alphabetise the segment `type`, sort on the quotation, fall back to
length. Each of those produces a sequence that looks like narrative order and is not, which
is worse than an admitted tie, because a reader uses this list to follow how a relationship
went. Unplaceable passages sort after placeable ones rather than being interleaved among
them, and passages that tie keep their stored order.

Segment `type` is never compared for the reason the schema gives for leaving it free text:
types are declared per work and are data. Alphabetising them would impose an order across a
corpus that the project has never claimed exists.

**The offset is consulted last, and only as a tie-break.** `selector.start` is documented as
a hint and never the authority, which is precisely the weight a final tie-break carries. It
is reached only once structure has run out, and never used to override a path.

**A consequence worth noting:** the "Evidence — n passages" field D25 added to the edge panel
is gone, replaced by the heading over the list. A field stating the length of a list printed
directly beneath it is the same fact twice. The count remains on the character panel, which
has no list.

*Reversible* cheaply — one comparator, and the fallbacks are the cases where it returns 0.

**Not addressed here.** Characters may carry evidence too, and the panel still reports only
a count for them. The bullet says per edge, the phase acceptance is about reaching a passage
from an edge, and no snapshot the project has yet produced puts evidence on a character.

---

## D27 — The passage is opened where the verbatim rule lives, and refused where the structure cannot be reproduced

**Phase 2.3.** `GET /api/snapshots/{id}/passage` returns the source text a piece of evidence
names, together with the offsets of the quotation inside it. The client cuts the text into
three at those offsets and marks the middle. It never searches for the quotation itself.

**Why the server finds it.** Invariant 3 defines "verbatim" against whitespace-normalised
text — runs of whitespace collapse to one space, nothing else is altered — and
`dramatis.text` is where that definition lives. A browser searching the passage for the
stored quotation would fail on nearly every quotation in a plain-text novel, because the
source is hard-wrapped and the stored string carries the line breaks: `he looked for a
moment at\nElizabeth` is not found in text where that newline is a space. The fix is not to
normalise in TypeScript as well. That would put a second copy of Invariant 3 in a second
language, and the copy nobody tests is the one that drifts.

The response carries text and a span rather than marked-up text, so the client decides how a
highlight looks and no manuscript passes through a string-replacement step on its way to the
DOM.

**Evidence is addressed by its stored position, not by its quotation.** A locator and a
quotation in a query string would put lines of an unpublished manuscript into every access
log that saw the request. The server already holds the document; the client sends an index.
The index is the piece's place in the *stored* array and not in the reading-ordered list
D26 produces, since those differ and both are numbers in range.

**A quotation that runs past its passage widens the window.** `verification` attributes a
quotation to the passage it *begins* in and accepts one that crosses a paragraph break, so a
quotation is not always contained by the passage its locator names. The window grows forward
one passage at a time and stops as soon as enough text has been added that the quotation
would have been found if it were there. One of the 1,022 quotations in the first full-novel
run needs this: a title page whose lines are separate blocks.

### The refusal, which is the substantive half

**A structure that cannot be reproduced is refused, and refused more widely than strictly
necessary.** A work stores the *names* of its segment types and never the rules that found
them. Only one name identifies its rule: the blank-line default, which a work either never
overrode or overrode with the default's own type. Everything else raises, and the endpoint
answers 501 with a sentence naming the division it could not reproduce.

The tempting alternative was to treat a single declared type as "flat, therefore
reproducible", and divide on blank lines under that name. That is the worst answer
available. A work divided into chapters would have its blank-line blocks *called* chapters,
and opening "chapter 3" would show the third paragraph of the title page with every
appearance of having worked. A refusal costs a feature. A confident wrong passage costs the
reader their trust in every other passage the tool ever shows them, and leaves no trace that
would let them find out.

**This is why fixture A cannot be opened, and why that is correct.** Fixture **A** is
hand-authored against chapters — "chapter 3" is the Meryton assembly, "chapter 34" the
Hunsford proposal — and its work declares `chapter › paragraph`. The only division the
project can currently derive from that store is 2,509 blank-line blocks, in which block 3 is
part of the title page. So phase 2's acceptance sentence, *from any edge in fixture A a user
reaches the exact supporting passage in two clicks*, is not met by this bullet and cannot be
met by any bullet in phase 2. It needs segmentation rules to have somewhere to live, which
is the structure map of **4.1**–**4.2**.

What *is* met is the same sentence against a real analysis: the first full-novel run
declares no segment types, so its blank-line division is exactly reproducible, and every one
of its 1,022 quotations opens at the right passage highlighted. The acceptance is recorded
as blocked rather than quietly reinterpreted, in the same way D24 recorded phase 1's.

*Reversible* cheaply. The refusal is one comparison in `spec_for_types`, and it widens the
moment a stored structure map gives it something better to consult.

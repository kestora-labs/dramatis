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

---

## D28 — Re-anchoring says which rung found the quotation, and refuses rather than reaching

**Phase 2.4.** `reanchor` locates a stored selector in a text that may have changed under it,
by exact match, then by the surrounding context, then by similarity. What it returns names
the rung that answered, and the reader is told whenever that is anything but the first.

**Each rung is a weaker claim than the one above.** An exact match is the quotation, found.
A context match is one of several occurrences, chosen by the text stored around it. A fuzzy
match is a passage that resembles the quotation and is not it. Rendering all three the same
way would be the failure this project keeps naming in other forms: something that looks like
information and is not. So the response carries `method` and `similarity`, the reader shows a
sentence for anything below exact, and an approximate highlight is drawn differently from a
verbatim one.

**A quotation that cannot be found has no anchor.** Below the similarity threshold nothing
is offered. An evidence list that silently re-pointed at whatever passage scored highest
would make every citation in the project unfalsifiable — the reader would have no way to
tell a recovered quotation from an invented one, which is precisely what Invariant 3's
verification gate exists to prevent at the other end of the pipeline.

**The recorded position is tried first, and accepted only if the quotation sits wholly
inside it.** A result that had to widen past the named passage is ambiguous between the two
cases widening covers: a quotation genuinely spanning a paragraph break, and one an edit has
pushed into the following passage. Re-anchoring tells them apart, agreeing with the fast path
in the first case and correcting it in the second — so a moved passage is reported as moved
rather than as a wide window at the old address.

### Three things that were wrong first, and what they cost

**Stripping the stored context threw away the only thing that could disambiguate.** A stored
prefix ends with the space joining it to the quotation; `normalise_whitespace` strips ends,
so every comparison disagreed on the first character tried and every occurrence scored zero.
The context rung silently degraded to "pick the first", which is exactly the behaviour it
exists to avoid, and it would have looked like it was working.

**Fixed-length fragments lose a short quotation to a one-word edit.** Candidate positions are
found from fragments cut out of the quotation. At a fixed 24 characters, every fragment of a
25-character quotation spans the same edit, and the passage becomes unrecoverable for being
short rather than for being changed. Fragments now scale with the quotation and are sampled
across it rather than taken from the ends.

**Trimming candidates by position preferred the opening chapters.** When a fragment matches
more places than the ceiling allows, keeping the lowest offsets is an arbitrary preference
for the front of the book. The stored offset hint decides what survives the cut instead.

All three had the same shape: a plausible-looking answer that was wrong in a way no user
could have detected. They are recorded because the tests that catch them are the point of
the tests, not because the code is interesting.

### What this does not do

**The client cannot yet choose which revision to read against.** The endpoint takes one and
defaults to the snapshot's own, where re-anchoring is a no-op and the fast path always wins.
Offering a reader a way to say *show me this evidence against the current draft* means a way
to choose a revision, which is **phase 3**'s subject. Until then the ladder's rungs below
`exact` are reachable through the API rather than through the interface, and the interface
renders them correctly when they arrive.

**`AddressableText` moved from `verification` into `segmentation`.** Both modules need to map
an offset back to the passage it falls in, and two copies of that mapping is the arrangement
this project keeps arguing against. No behaviour changed; `verification`'s tests pin it.

*Reversible* cheaply. The rungs are three branches in one function, and the thresholds are
module-level constants with tests pinning the properties rather than the values.

---

## D29 — A filter is offered only when the snapshot gives it something to distinguish

**Phase 2.5.** The graph can be narrowed by minimum weight, by relation type, and by
provenance. Each control appears only when the snapshot in view can actually be divided by
it, and where none of the three can, the section is absent rather than present and inert.

**This is not a tidiness preference; it is the same rule as D25.** The first full-novel run
records *no relation types at all* across its 241 relations, and exactly one provenance. A
type control there would be an empty list, and a provenance control a single choice that
changes nothing. Both would tell a reader the graph can be narrowed in ways it cannot,
which is the failure this project keeps meeting in different clothes: a control that looks
like information and is not. The hand-authored fixture carries eight types, and there the
same control is worth having.

**The weight filter is withheld when weights are not comparable.** A snapshot mixing two
weight bases has no single scale for "at least twenty" to be measured against, so no floor
can be set on it — the same reason 2.1 never prints a weight without its basis. It is
withheld again when every weight is identical, where a floor either keeps everything or
removes everything.

**An untyped relation is kept by an empty type filter and dropped by any other.** Asked for
the kinship edges, a reader is not asking for the edges nobody typed. Since the real run
types nothing, the type filter is all-or-nothing there — which is why the control does not
appear there at all.

### Which characters disappear, and which do not

A filter applies to relations. A character is in the picture if any of its relations is. So
a character the filter empties is **hidden and counted**, while a character the *snapshot*
left with no relations is **still drawn, dimmed**.

The distinction is the whole of it. Having no relations at all is a fact about the analysis
— eighteen characters in the full-novel run are in that position, Coleridge and Fielding and
the rest of the critical preface's cast, and they are exactly the finding D-notes have been
circling since the first run. Hiding them because a slider moved would delete evidence about
the analysis. Losing your relations to a filter is a fact about the filter, and drawing
eighty-one dimmed dots would bury the structure the filter was applied to reveal. The tally
underneath says how many went, so neither disappears silently.

### The slider commits on release, and why it has to

Applying the weight on every input event rebuilds the graph and re-runs the force layout.
That layout is **775 ms on the full-novel graph**, measured, and it blocks the main thread;
a single drag produces dozens of input events. So the slider holds its own value, the label
follows the thumb, and the filter is applied once when the thumb is let go.

The alternative — updating the graph continuously — is not merely slower but wrong for this
layout: `cose` is a force simulation with no memory between runs, so every intermediate
value would fling the whole graph into a different arrangement. Keeping positions across a
filter change is **2.6**, and until it exists a filter is a deliberate act rather than
something a reader scrubs through.

*Reversible* cheaply. The filters are one predicate and a shape of three fields; the
controls read their options from the snapshot rather than from a list anybody maintains.

**Not covered by any corpus the project holds.** Neither store has more than one provenance
among its relations, so the provenance control has never been drawn outside a unit test.
It will first appear for real in **phase 4**, where reference documents yield `asserted`
relations alongside `observed` ones — which is precisely the corpus that motivates it.

---

## D30 — A pinned layout lives in the browser, because `serve` does not write

**Phase 2.6.** The graph offers five layouts and a pin. Pinning records where every drawn
character sits; from then on the snapshot reopens in that arrangement, dragging a character
updates it, and choosing a different layout releases it.

**Why a pin is needed at all.** A force layout is a simulation with no memory. Run it twice
on the same graph and the same characters land somewhere else. A reader who has spent a
minute working out where the Bennets are loses that the moment anything is redrawn — a
filter moved, the page reloaded — and the picture they were reading is gone for no reason
they caused. Measured on the full-novel graph, that recomputation also costs **775 ms** of
blocked main thread each time.

**Where it is kept was the real question, and the answer is: not in the project file.**
`dramatis serve` reads and never writes. That is stated in its own help text and is a
property people rely on when the file in question is an unpublished manuscript: a loopback
server that can only read is a much smaller thing to reason about than one that can also
modify the store. Adding the first write endpoint to that server, so a graph can remember
where it put Mr Collins, is not a trade this bullet is entitled to make.

So a pin lives in `localStorage`, keyed by snapshot id. The cost is real and worth stating
plainly: **a pin does not travel with the project file.** Copy the file to another machine
and the arrangement does not come with it, which sits awkwardly beside D15's "copy it and
you have copied the project". That is the right thing to revisit in **phase 6**, where a
figure somebody else can reproduce is the actual requirement and a written-through layout
would be earning its keep rather than buying convenience.

**A pin is per snapshot.** Two snapshots of one work are two different graphs, and sharing
an arrangement would place characters at coordinates belonging to a different analysis.

### The half that took two attempts

**Nodes with no pinned position are placed by locking the pinned ones, not by leaving them
out of the layout.** A pin taken while the graph is filtered knows nothing about the
characters that were hidden, so loosening the filter presents nodes the pin cannot place.
The first attempt ran the layout over just those loose nodes. That is wrong in a way worth
recording: a collection of nodes without their edges gives a force simulation nothing to
pull against, so it scattered them as though the graph had no structure — and in the
unpinned case, where *every* node is loose, it laid the whole graph out with no edges at
all and put every character in a tidy meaningless grid.

Locking is the correct mechanism. The pinned nodes stay exactly where they are and act as
the fixed points the newcomers are arranged around, with all the edges present to do the
arranging. Verified on the real graph: pinned while filtered to 25 characters, then
unfiltered to 102 — the 25 moved **0 px** and the other 77 arranged themselves around them.

**Newcomers are arranged by the layout the pin was taken under**, not by whatever the
control is showing, or they would be placed on a different principle from their neighbours.

**An unreadable pin is treated as absent rather than repaired.** Stored JSON can be left
behind by an older client, a half-finished write, or a person with the developer tools open.
Ignoring it costs one relayout; trusting it puts characters on top of each other at
coordinates that mean nothing. Storage that refuses to answer at all — a private window, a
quota policy — is likewise just "no pin", and never an error the reader has to see.

### What this fixes from 2.5

D29 accepted that a filter change re-runs the layout and moves everyone, and said holding
positions across one was 2.6's job. It is now done: with a pin in place, narrowing the graph
moves nothing and runs no layout at all. Without one, the behaviour is unchanged.

*Reversible* cheaply. The catalogue is one array, the storage is three functions behind an
interface a test can replace, and nothing else in the client knows where a pin is kept.

---

## D31 — A preface is a region somebody confirmed, not a rule; and the server learns to write

**Amends 4.1 and 4.2, adds 4.8 and 4.9.** Prompted by the first full-novel run, where a
third of the cast turned out to be people the 1894 edition's critical preface discusses
rather than anyone in the novel.

### The measurement that started it

Of 102 characters in `snap:9204c78ca953`, **38** are evidenced only before the novel begins:
Coleridge, Whitman, Scott, Smollett, Fielding, Mary Wollstonecraft, Maupassant, the painters
Memling, Meissonier and Cosway, and a scattering of characters from Austen's *other* novels —
Miss Bates, Mrs Norris, John Thorpe, Mrs Musgrove, Edmund. Eighteen of them have no relations
at all. They account for 23 of 241 relations. Remove them and the cast is 64, against the
fixture README's estimate of "roughly sixty named characters".

The preface is 34,287 characters, **4.7%** of the file, three of sixty-one extraction windows,
about **$0.22** of the run's $4.49. So the case for excluding it is not the tokens. It is that
5% of the spend produces 37% of the cast, and that **D22** sizes the resolution budget from the
number of surface forms — 150 in that run. Cutting 38 characters shrinks the one call in this
pipeline that has already failed once on size.

### Where the exclusion lives

**In the structure map, as a region — not as a project setting, and not as a definition.**

The tempting version is a switch: *analyse the preface, yes or no*, with a rule saying what a
preface is. This project has twice refused that shape. **D7** rejected stop-lists because they
encode one language's conventions into the core. **D24** deferred 7.7 because deciding what
counts as a name is a question that needs measuring, not guessing. A front-matter rule is the
same object: Project Gutenberg alone yields `PREFACE`, `INTRODUCTION`, transcriber's notes,
frontispiece lists and publisher's advertisements, in one language, from one source.

**4.2 already contained the answer** and only needed widening. The model proposes where the
narrative begins, the user confirms or corrects it, and the answer persists as a property of
*that document*. Nothing is written down about documents in general. The single amendment is
that a structure map addresses **regions within a document**, not only whole documents —
without which the mechanism cannot reach a preface bound into the same file as the novel,
which is the commonest shape a public-domain text arrives in.

### The server acquires writes, and this is not an invariant question

Recorded because it was got wrong in discussion first. The concern raised was that a project
UI would compromise `serve`'s guarantees. Checked against the actual code, it does not:

- **No model is involved.** `ingest.py` imports `ids`, `store` and `text`, and no provider.
  Creating a project is a filename, three settings and a hash. Only `analyse` calls a model,
  and it stays a separate act.
- **Invariant 6** says *reading* never requires a model, which is untouched. **Invariant 7**
  forbids egress except to the user's chosen provider, and there is no egress at all here.
- The operations are not new. `ingest` already creates a store file; `set_setting` already
  writes settings. What is new is an HTTP caller for them.

What is actually true is narrower: `serve --help` says *"Reads only: it never calls a model"*,
and the first clause stops being true. That is a promise one command makes about itself, in a
help string, and it is revised rather than defended. The read-only property was never enforced
in any case — `Store.open()` is a plain read-write `sqlite3.connect`, and the guarantee held by
absence of write endpoints, not by mechanism.

It was also always going to end: **5.1** puts review status on every node and edge and **5.2**
requires corrections to persist. A correctable graph in a browser is a server that writes.

### The guard that does matter

A browser will send a cross-origin POST to `127.0.0.1` from any page the user happens to have
open. The attacker cannot read the reply, but the side effect lands. Today that is harmless
because nothing mutates; from **4.8** it would not be. So every mutating endpoint checks the
request's `Origin` against the server's own, and that is settled at the first write rather than
retrofitted once there are a dozen.

This is the one real technical consequence of the change, and it is a few lines rather than a
redesign.

### Scope

**4.9** covers a source, a store name, the collectives setting, and confirming the regions from
**4.2**. Prompt selection is deliberately left out: choosing or authoring a prompt version means
prompts are versioned artefacts under file-and-hash discipline, which is **7.4** and **7.5**,
and offering the choice earlier would let a project record a prompt nothing can compare against.

*Reversible.* 4.8 and 4.9 are unbuilt bullets; the amendment to 4.1 and 4.2 is a widening that
costs nothing if the region case never arises.

---

## D32 — A document is one version of a file, and a file's identity is its path

**Phase 3.1.** `ingest_folder` takes a folder as one text revision of many documents, and
document identifiers now carry a content hash. Both halves of the bullet turn on the same
change, and it began as a defect rather than a feature.

### The defect

`ids.document_id` derived an identifier from the filename alone, and `upsert_document`
overwrites content on conflict. So a second ingest of an edited file landed on the same row
and **rewrote the text an earlier revision pointed at**. Demonstrated before the change:

```
revision A: rev:02904c1e0563   'Ada met Bram at the gate.'
revision B: rev:c85a893869ea   'Ada met Cai at the gate instead.'

text of revision A afterwards:  'Ada met Cai at the gate instead.'
rev:02904c1e0563  recorded 02904c1e  |  actual now c85a893869  |  MISMATCH
```

Nothing raised. The older revision reported text it had never held, its recorded hash no
longer described what it returned, and every quotation anchored into it — the whole of
2.2, 2.3 and 2.4 — cited a text that did not exist. It was invisible while a work was one
file that nobody re-ingested, which is exactly how phases 1 and 2 exercised it.

Phase 3 cannot be built on top of it: diffing two revisions is meaningless if the older one
changes when the newer arrives.

### The fix, and what it settles

**A document row is one version of a file.** The identifier is `doc:<name>-<sha12>`, so
edited content becomes a new row and the old one stays where the old revision left it. The
name is kept in front of the hash because a human tracing an evidence locator back to a file
should be able to read it.

Idempotence is unaffected and slightly strengthened: the same bytes always yield the same
identifier, which is what `ingest` opens by promising.

**A file's identity across revisions is its path**, relative to the folder ingested. That is
what makes `chapter-03.md` the same chapter in two drafts however much of it was rewritten,
and it is what per-file tracking compares. Unchanged files are therefore *shared* between
revisions rather than copied — on fixture **B**, two drafts of three chapters produce four
document rows, not six, and the two that are shared are exactly the two the fixture says
were untouched.

### Folder ingest infers nothing

The folder pointed at is the revision. `ingest_folder` does not decide that `draft-2/` is a
revision of `draft-1/`, or that `cast.md` is reference material — fixture **B** states its
directory-per-revision layout as data specifically so that no code has to know it, and
classifying documents is **4.1**'s job.

**What it will not read, it names.** A file that is not text, is empty, or is not UTF-8 is
skipped and reported rather than passed over. A revision quietly missing a chapter is a
graph missing a character with nothing on screen to say why. One unreadable file does not
discard a folder the user meant to ingest.

### The second defect, which only multi-file ingest could expose

`pipeline.analyse` passed `revision.document_ids[0]` as the document for every piece of
evidence. With one document per revision that was indistinguishable from correct. With a
folder it attributes every quotation in the novel to chapter one.

Evidence is now attributed by where its passage falls, using a span map returned by
`Store.revision_document_spans`. That lives beside `revision_text` deliberately: the map and
the concatenation have to agree about how documents are joined, and a caller deriving
"documents, in order, end to end" for itself would be a second place for that to be wrong.
An offset no document covers is left unattributed rather than given to the nearest.

*Reversible* — but not cheaply, and not without cost. Identifiers minted under the old scheme
remain valid and are still resolvable; reverting would reintroduce the overwrite. Stores
written before this change keep whatever they already hold, and the first re-ingest of an
edited file under the new scheme adds a row rather than destroying one.

---

## D33 — Snapshots are compared by analysis *configuration*, not by run identifier

**Phase 3.2.** A work's snapshots are served with its two time axes as separate lists, and
drawn as a grid: a row per text revision, a column per analysis *reading*. Reading across a
row holds the text still and varies the analysis; reading down a column does the reverse.
That is Invariant 4 drawn rather than described.

### Why a column is not a run

A run identifier includes when it ran. That is deliberate and documented — *"two executions
of the same configuration are two runs, not one: models are not deterministic"* — and it is
right for identity. It is wrong for comparison. Since no two runs are ever equal, asking
whether two snapshots differ by text or by analysis and answering from run identifiers
returns **both, every time**, which is precisely the answer the invariant exists to prevent.

So a column is a *configuration*: the model, the prompt actually sent, the pipeline, and the
parameters the run was given. Everything except when somebody pressed go. Two executions of
one configuration share a column, which is what lets a reader hold the analysis still.

### What that exposed, and why 3.6 is now marked blocked

On real data the grouping does not group. Fixture **B** analysed twice under identical
settings produces two configurations, because `parameters.resolution_prompt_version` is
`resolve-v1` for the first analysis and `null` for the second — the registry was already
populated, so resolution never called a model.

That field records what the run **did**, not what it was **asked to do**. As a component of
run identity it means an analysis cannot be held constant across two revisions, which is
exactly what **3.6** requires and what the phase acceptance depends on: *"the diff attributes
every change to the text revision"* cannot be satisfied while the second analysis is, by
record, a different analysis.

The fix belongs in 3.6 and is not attempted here — it is a change to what a run records
about itself, and doing it inside a bullet about listing snapshots would be deciding the
shape of run identity as a side effect of building a table. 3.6 carries the note.

### Two smaller things this bullet found

**Revisions were listed in an order decided by hashing.** `list_text_revisions` broke ties on
`created_at` with the identifier, and a revision identifier is a content hash. Two drafts
ingested in the same second — a folder read after another, which is the ordinary case — came
back with "Second draft" above "First draft". Ties now fall back to insertion order. It was
harmless while nothing displayed the order; 3.2 is the first thing that does.

**Two readings could render under the same caption.** A caption is the model and the prompt
version, and two readings can share both while differing in effort or window size. Two
columns captioned identically read as a duplicate rather than as a distinction, so where a
caption would repeat, the configuration digest is appended. It does not say *what* differs —
that needs the parameters, and belongs with 3.6 — but it is honest that the two are not the
same, which a repeated caption is not.

### An empty cell is drawn

Not every revision has been read by every configuration. A gap says so, and it is a
different fact from a pairing that was analysed and produced nothing new — a distinction a
list of what exists cannot express at all.

*Reversible* cheaply. The endpoint is additive, the grid is one pure function, and the
configuration digest is computed on read rather than stored, so nothing in the store depends
on it.

---

## D34 — A diff reports attribution first, and refuses the comparisons it cannot make

**Phase 3.3.** `diff_snapshots` compares two snapshot documents and returns characters
added, removed, merged or split, and relations added, removed, strengthened, weakened or
retyped. Ahead of any of that it returns **attribution**: which of the two axes the change
can be laid at.

The order is the point. Fixture **B** says so before it says anything about the changes
themselves — *"The attribution matters as much as the change. Both drafts must be analysed
by the same run configuration, or the diff cannot distinguish a rewrite from a better
prompt."* A list of edges that moved is not a finding until something says what moved them.

### Four refusals

**Different works cannot be diffed.** Two novels share no characters by construction, so
every node and edge would be reported as added or removed. The result would be a list of
everything, wearing the shape of a diff.

**Weights are compared only within a shared basis.** A weight is a number on a named scale.
Where two snapshots disagree about the scale, nothing is reported as strengthened or
weakened and the reason is given — the same refusal 2.1 makes when printing a weight and
2.5 makes when offering to filter on one. Retypings are still reported, because a type does
not live on the weight scale.

**Both axes moving credits neither.** Picking whichever moved more would be inventing an
attribution the evidence does not support.

**Identity is claimed from the record, not guessed.** A merge is recognised because the
surviving character now lists the absorbed one's name among its own surface forms, which is
what the registry writes down when it merges two. Absent that record the change is reported
as a plain removal and addition, because *these two are the same person* is a claim, and an
unevidenced one is worse than an unexplained pair. A split is only a split if the character
it came out of is still present; otherwise the pair is a rename, and calling it a split
would invent a second person.

### Relations are compared through a merge

Two characters becoming one would otherwise report every relation touching the absorbed
character as removed, and a matching one as added — dozens of spurious changes describing a
single act of curation. So a relation's identity for comparison is its pair of endpoints
seen *through* the merge map, not its stored identifier, which was derived from the names
the endpoints had at the time. A real weight change across a merge is still seen.

**One relation, one entry.** An edge that strengthens *and* is retyped is reported once
carrying both, since two entries would double-count a single change.

### The analysis axis is compared by configuration

Consistent with **D33**: a run identifier includes when it ran, so comparing identifiers
would call two executions of one configuration two different analyses and then credit every
change to nothing at all. Where the document carries its run — the schema does not require
it — the comparison uses model, prompt, prompt hash, pipeline and parameters. Where it does
not, the identifier is the fallback, on the ground that an attribution too strict is better
than one too generous.

### What this confirmed about 3.6

The diff works. Run against fixture **B**'s two drafts analysed under identical settings, it
reports exactly what `corpus.json` predicts: the Auber/Idris edge weakened, the Neve/Idris
and Auber/Neve edges strengthened, no character changes, nothing else.

And it answers **both**, because the two runs differ in exactly one recorded field:

```
param differs: resolution_prompt_version -> resolve-v1 | None
```

Every other part of the configuration matches. The second analysis found the registry
already populated, so resolution never called a model, and the field records what the run
*did* rather than what it was *asked to do*. One field, recording an outcome rather than an
instruction, is the whole distance between phase 3's acceptance sentence and where it stands.

That is not a defect in the diff, and it is not fixed here: it is a change to what a run
records about itself, which is **3.6**'s subject. The roadmap's acceptance now carries the
status rather than reading as though it were met.

*Reversible* cheaply. The module is pure over two documents and holds no state; the endpoint
is additive.

---

## D35 — A run records what it was asked to do, not what happened to it

**Phase 3.6**, taken out of order because it blocked the acceptance for 3.2 and 3.3.

`parameters["resolution_prompt_version"]` recorded `Resolution.prompt_version`, which is
null whenever resolution answered from the character registry without consulting a model.
That happens on every analysis after the first. It now records the version the run was
*configured* to use, which it was configured to use whether or not it turned out to need it.

### Why one field mattered this much

`parameters` is the material a run's identity is hashed from. An outcome recorded there
makes two analyses of one configuration into two configurations — and then a diff between
them can credit nothing to either axis, because both appear to have moved.

That is not a cosmetic defect. Phase 3's acceptance is *"the diff attributes every change to
the text revision"*, and fixture **B** says the same thing in its own words before it lists
a single expected change: *"Both drafts must be analysed by the same run configuration, or
the diff cannot distinguish a rewrite from a better prompt."* Holding the analysis still
across two revisions was not expressible, so the sentence could not be satisfied by any
amount of work in 3.2 or 3.3. Both bullets recorded it and moved on; this is where it is
paid.

Measured before and after, on fixture **B**'s two drafts analysed under identical settings:

```
before   attribution: both   (param differs: resolution_prompt_version -> resolve-v1 | None)
after    attribution: text   (no warnings)
```

Both directions now hold. Two revisions a month apart under one configuration attribute to
the **text**; one revision under two different settings attributes to the **analysis**.

### The prior position, and why it is changed rather than overruled

A test asserted the old behaviour deliberately, with reasons: *"Not a flaw — the second run
genuinely does less work, and says so. The graph it produces is the same; the run that
produced it is not, and both facts belong in the record."*

The first half of that is right and is kept. The second confuses two claims. *This run did
less work* is an outcome, and it remains observable — `Resolution.prompt_version` is still
null when no model was consulted, and the test still asserts it. *This run was a different
analysis* is a claim about configuration, and it was not true: nothing about the analysis
differed, only the state of the registry it happened to meet. Both facts do belong in the
record; they do not both belong in the identity.

What is lost is the archived note that a particular run skipped the resolution call. That is
a real loss, accepted deliberately: an outcome sitting inside identity material is what
broke comparability, and phase 5's review work is a better home for execution detail than
the field a run is hashed from.

### `--like`, so the guarantee is reachable

`dramatis analyse REVISION --like SNAPSHOT` reads that snapshot's recorded settings and uses
them. *I passed the same flags* is not the same claim as *the run recorded the same
configuration*, and only the second is what a diff needs.

Settings given explicitly still win — the point is to make holding the analysis still the
easy path, not to make changing it impossible — and an override is announced, because a
re-run that quietly ignored half of what it was told to copy would be worse than one that
never offered. `--effort` therefore no longer defaults to `medium` in the parser: it
defaults to nothing, so an instruction can be told from an absence.

*Reversible* cheaply in code, and not in effect: snapshots written before this change carry
the old parameters and will not compare with ones written after. That is the same class of
break D19 warned about for the collectives setting, and for the same reason — the record of
what a run was is what makes two of them comparable.

---

## D36 — The overlay is drawn over the union of both snapshots, and the two renderings answer different questions

**Phase 3.4.** A diff is shown twice: as marks on the graph, and as a list. Both carry the
attribution sentence D34 put first.

**Two renderings, because they answer different questions.** The overlay answers *where* —
which part of the cast moved, and whether the movement is central or at the edges. The list
answers *what*, in an order somebody can go down and check. Neither substitutes for the
other: a graph cannot say "25 to 4", and a list cannot show that both edges that moved meet
at the same character.

**The overlay is drawn over the union of both graphs, not over the later one.** A relation
that was removed does not exist in the newer snapshot, so drawing only what is there would
omit exactly the half of the diff a reader is least able to reconstruct. Demonstrated: two
snapshots where a minor character disappears, the later graph holding two characters, the
overlay drawing three and marking the third and its edge as removed.

**One class per element, by a stated rule.** An edge that both strengthened and was retyped
is drawn as the change that moved it, in the order removed, added, weakened, strengthened,
retyped. A picture cannot say two things about one line at once, and choosing silently would
be worse than choosing by a rule somebody can read. The list says both.

**The union is a document, so the existing graph machinery scales it.** The overlay reads as
the same picture with marks on it rather than as a second kind of diagram with its own
conventions — sizes, widths and the square-root scaling all come from `buildGraph` unchanged.

**Comparison is a second choice about a graph already on screen**, so it is shift-click on
the lineage grid rather than a mode with its own controls. The earlier snapshot is always
the one compared *from*, whichever cell was shift-clicked, so the diff reads forwards.

### A defect this bullet exposed

`list_snapshots` broke `created_at` ties with the identifier, and a snapshot identifier is a
content hash. Two snapshots written in the same second therefore came back in an order
decided by hashing — and 3.4 reads that order to decide which snapshot a diff runs *from*.

The first live run of the overlay showed every strengthening as a weakening and every
weakening as a strengthening, because the two snapshots were being compared backwards.

This is the same defect D33 fixed for text revisions and analysis runs in 3.2, left in place
for snapshots because nothing then depended on their order. Ties now break on insertion
order, as the other two do.

*Reversible* cheaply. The overlay is a pure function over two documents and a diff, and the
marks are classes on elements the graph would draw anyway.

---

## D37 — Widths are measured against the snapshot, not against what survived the filter

**Phase 3.5.** Edge width and node size are scaled against the heaviest relation in the
snapshot rather than the heaviest one currently drawn. The old behaviour is kept as a
`relative` option; `absolute` is the default.

### What the default prevents

Under relative scaling the reference moves whenever the view narrows. Filter the heaviest
edge away and every remaining edge thickens — nothing about the work changed, but the graph
now says the survivors are more central than it said a moment ago, and there is nothing on
screen to say why.

Measured on fixture **A**, filtering to `kinship` so the weight-100 romantic edge drops out:

```
Elizabeth — Jane, weight 75

unfiltered              width 12.26
filtered, absolute      width 12.26     unchanged
filtered, relative      width 14.0      now reads as the heaviest edge in the work
```

The same failure applies to node size, so the same reference governs both. `graph.ts`
already required the two encodings to "read consistently rather than one compressing while
the other does not", and scaling one against the snapshot while the other floated with the
view would break that.

**A node whose degree genuinely changes still changes size.** Filtering an edge away really
does leave a character less connected in that view; that is the data, not the scale. What
absolute holds still is the *reference* — a character the filter did not touch keeps its
dot, which relative does not manage.

### Why relative is kept rather than removed

A narrowed view using its full range is what a reader studying one filtered slice wants, and
it is the honest choice once the reader knows the slice is the subject. The problem was never
that relative is wrong; it is that it was the only option and its failure is invisible. Both
are offered and the panel says which reference is in force and what that means.

### Where the choice does nothing, which is worth knowing

On a snapshot whose only usable filter is the minimum-weight floor, the toggle cannot change
anything: a floor never removes the maximum, so the drawn maximum is always the snapshot
maximum. The first full-novel run is exactly that snapshot — no relation types and a single
provenance (**D29**), so the weight slider is its only control.

The setting therefore begins to matter when a filter can remove the heaviest edge, which
means relation types or a second provenance — the fixture has the first, and phase 4 brings
the second. This is not an argument against the default; it is the reason the default costs
nothing today and starts paying the moment a corpus has something to filter by.

*Reversible* cheaply. One parameter with a default, threaded through the one function that
decides how a graph looks.

---

## D38 — The structure map proposes only what a folder can actually evidence

**Phase 4.1.** `propose_structure` reads a folder and returns a map: for each document, how
it is addressed, whether it appears to be a revision of another, and what role it plays.
Every answer carries the evidence it rests on, because **4.2** asks somebody to confirm it,
and *confirm this* is unanswerable without being told what the proposal was made from.

### Role is not proposed, and that is the bullet's substance

Fixture **C** is built to punish reading filing conventions. Its reference material sits in
`series-bible/`, its narrative in `transmissions/`, its revisions in YAML frontmatter, its
units are numbered `t01` — and its README says what happens if any of that leaks into the
core: *"Invariant 1 or the 'not tied to one author's method' non-goal has been broken."*

A folder offers exactly one signal about role, and it is the names somebody chose. So role
comes back `unknown`, with the reason recorded and 4.2 named as where it is answered. An
honest unknown is worth more than a guess that happens to be right on the two corpora
somebody tested. Six tests exist solely to spring that trap.

The same reasoning gives regions the honest floor: one region covering the whole document.
**D31** widened the map to hold regions so a preface bound into a novel can be classified
apart from it, and finding where such a region ends means reading the text.

### The filename carries a revision, and similarity only describes it

Two files named `chapter-03.md` in sibling folders are the same chapter. They remain the
same chapter when the text has been rewritten, because that is what revising means — so
similarity is measured and reported, never used as a gate. A chapter thrown away and written
again would score like a stranger while being the revision a reader most needs recorded.

### A measurement error that nearly became a documented finding

I first gated on similarity, and fixture **B**'s rewritten chapter measured `0.054` —
below two unrelated documents at `0.040`. I wrote that up as *no threshold can separate them*
and built the design on it.

It was an artefact. `difflib.SequenceMatcher` applies an `autojunk` heuristic to sequences
longer than 200, treating any element in more than 1% of them as noise — which, on a
sequence of characters, is every common letter. The ratio is then both meaningless and
asymmetric. The same pair measures:

```
0.054   ratio(draft-1, draft-2)     autojunk on
0.545   ratio(draft-2, draft-1)     autojunk on, arguments swapped
0.838   autojunk=False              the real figure
```

Measured properly, revisions score 0.838–1.000 and unrelated pairs 0.119–0.317. They
separate cleanly, and my documented conclusion had been exactly backwards.

The design did not change — the filename still carries the claim, for the reason above — but
the reasoning under it was rebuilt on numbers that are true, and `_similarity` now passes
`autojunk=False` with the incident recorded where somebody would remove the flag.

**Addressing is `section` and settled**, because that is the only division the project can
reproduce (**D27**). Proposing `chapter` would propose something the rest of the system
cannot honour.

**`dramatis structure FOLDER`** shows the map. It calls no model and writes nothing, so the
proposal can be read before anything is spent — which is most of the reason it is a separate
step from the ingest that will use it. Its output is ASCII, per the convention
`IngestResult.summary` states, with a test.

*Reversible* cheaply. Nothing is persisted; 4.2 is what stores a confirmed map.

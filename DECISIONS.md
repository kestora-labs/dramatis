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

## D39 — A structure map is confirmed by a person, and a boundary is stored as a quotation

**Phase 4.2.** **D38** left two questions open because a folder cannot evidence them: what
each document *is*, and where inside it the narrative begins. Both need the text read. This
bullet has a model read it, a person confirm or correct the answer, and the answer saved so
the next ingest of that folder does not ask again.

### Nothing defines a preface, including the prompt

The constraint is the bullet's own: *no convention is hardcoded — in particular, nothing
anywhere defines what a preface is*. The obvious way to break that is not a special case in
the code but a list in the prompt: `PREFACE`, `FOREWORD`, `INTRODUCTION`, `*** START OF THE
PROJECT GUTENBERG EBOOK`. That is a hardcoded convention wearing a prompt's clothes, and it
would work beautifully on the two corpora anybody tests it against.

So `prompts/structure.md` names no heading and describes no document type by title. It asks
what the document *does* — are relationships shown happening, or stated about — and says
outright that the question is about this document, not about documents. A test walks the
module's syntax tree and fails on any string literal outside a docstring containing
`preface`, `foreword`, `prologue`, `introduction` or `appendix`: the prose may discuss them,
the code may not match on them.

`unsure` is in the response schema for the same family of reasons. Constrained decoding
returns whichever values it is offered, so a two-value enum does not produce honest answers —
it produces a coin flip recorded as a classification, on exactly the documents a person most
needs to look at. `unsure` comes back as `unknown`, unsettled, and cannot be saved.

### The boundary is a quotation; the offset is a hint

A model that proposes a division returns the text the narrative begins and ends with, not a
character offset, and the region stores those quotations. They are found with `reanchor` —
the same ladder that finds an evidence quotation after the text has moved (**2.4**).

This is what makes *saved and reused* mean anything. The map is confirmed once and applied to
later ingests, by which time the author has edited the document and moved every offset in it.
The quotation still finds the boundary; the number does not. The test that matters adds a
paragraph to a preface and checks the narrative still starts in the right place.

A boundary that cannot be found is refused and reported, never guessed. The failure it
prevents is silent: a boundary in the wrong place removes a chapter from the analysis and
nothing on screen would say why. So the document falls back to one region, the role stays
confirmed, and a note says which half lapsed.

Region offsets index the **whitespace-normalised** text, because that is the only text a
quotation is ever anchored in. `DocumentPlan.characters` moved to match. Measuring a boundary
against one text and applying it to the other lands it wrong by however much the source was
hard-wrapped.

### A confirmed `unknown` would never be asked about again

`confirm` refuses to settle a document whose role is still unknown, and `save` refuses a map
that is not confirmed. Both refusals protect the same thing: a saved answer is not asked
about again, so storing a guess promotes it to a fact by the act of storing it — silently,
and permanently.

Corrections are validated before the map is inspected. A mistyped role is the user's most
recent action and the thing they can fix; complaining first about some other document's
missing answer sends them looking for a fault that is not there.

### Not a conversation

`dramatis structure FOLDER` gains `--ask`, `--set PATH=ROLE`, `--confirm` and `--forget`. It
is deliberately not interactive. The ingest command once asked questions on stdin and raised
`EOFError` wherever stdin was not a terminal, which is most places a CLI runs. `--set`
arguments can also be scripted, read back, and pasted into a bug report.

`--set` alone is a complete path: somebody who already knows what their folder holds should
not have to pay a model to be asked. Without `--ask` the command reaches no network; without
`--confirm` it writes nothing. Both are tested, because neither is visible in the output.

The saved map is keyed by the **resolved** folder path. `corpus` and `./corpus` and the same
folder reached from a parent directory are one folder, and a user who moved between them
would otherwise be asked the same questions twice.

### Where a saved map is actually spent

`ingest_folder` took one `role` for a whole folder, which cannot describe fixture **C**: its
reference material and its narrative sit side by side, and no single flag separates them. It
now gives each document the role somebody confirmed for it, falling back to the flag for
files nobody has answered for — a file added since the map was confirmed, most often. That is
what *reused on subsequent ingests* buys, and the result reports the count, because otherwise
nothing distinguishes a folder somebody classified from one that took a default.

Ingest reads the map through the store rather than by importing `structure`, which imports
`ingest`. The JSON shape is the coupling, and it is noted where it is read.

**4.1 shipped its subcommand with no CLI test**, which this bullet closes: `tests/
test_cli_structure.py` covers both commands' behaviour through `main`.

*Reversible.* The `structure_map` table is additive and read only by `structure_for`;
dropping it returns the system to **D38**'s behaviour, where every role is an open question.

---

## D40 — A document is a path and the content that was at it, and neither half identifies it alone

**Phase 3.7.** `ids.document_id` now takes the path a document is stored under as well as its
content hash. It is the completion of **D32** rather than a reversal of it: D32's own title
says a file's identity is its path, and then put only the filename's *stem* into the
identifier.

### The defect

`dramatis ingest fixtures/b` crashed:

```
sqlite3.IntegrityError: UNIQUE constraint failed:
  revision_documents.revision_id, revision_documents.document_id
```

`draft-1/chapter-01.md` and `draft-2/chapter-01.md` are byte-identical, so both resolved to
`doc:chapter-01-45cd6ac24d0d`, and `ingest_folder` appended that identifier twice to one
revision. `chapter-02` collides the same way; `chapter-03` does not, because it is the one
the fixture rewrote between drafts.

**This is not a hash collision.** It is two distinct documents that legitimately hold the
same text, which is not a corner case but the usual shape of a drafts folder: most chapters
are untouched between revisions. A rule that only fails on files nobody edited fails on
almost the whole corpus.

### The crash was the guard, not the fault

`revision_documents` is keyed on `(revision_id, document_id)`, and that key is the only
reason this surfaced as an error rather than as a wrong graph. Without it, `upsert_document`
would have been called twice for one identifier and the surviving row would carry whichever
path was written last — `draft-2/chapter-01.md`. Every consequence follows from that one
row standing in for two files:

- `revision_document_spans` returns spans in position order, so a quotation from the first
  draft's chapter one falls in the first span — and that span names a document whose path
  says `draft-2`. The evidence mis-attribution **D32** fixed, restaged.
- `_previous_state` maps path to hash from the revision's documents, so the first draft's
  chapter one would have no path at all, and per-file tracking could not report it.
- `structure.propose_structure` proposes `draft-2/chapter-01.md` as a revision *of*
  `draft-1/chapter-01.md` — two documents, one derived from the other. Under content-only
  identity the store held one document that was its own revision.

### What identity is now

`doc:<slug(path)>-<sha12>`, where the hash covers the path as well as the content.

**The path is the one the document is stored under, relative to the folder ingested.** That
is what preserves D32's property, and it is worth being explicit because an absolute path
would destroy it: `chapter-01.md` untouched between two drafts *ingested as separate folders*
is the same relative path in both, keeps one identifier, and is shared between the two
revisions. Fixture **B** still produces four document rows for two drafts of three chapters,
and the two shared rows are still exactly the two the fixture says were untouched. Ingesting
the parent folder instead is a different sentence — one revision of eight documents — and now
answers it rather than raising.

`ingest_file` and `ingest_folder` both derive the identifier from the same string they write
to the `path` column, so identity and that column cannot disagree about where a document is.

**The hash covers the path, and this is not belt-and-braces.** `slugify` collapses `/`, `\`,
whitespace and `_` to a single `-` and truncates at `MAX_SLUG_LENGTH`, so two genuinely
different paths can reduce to one token — and truncation makes that reachable rather than
theoretical: two stub chapters deep in a long folder name, both holding `TBD`, is an ordinary
thing to find in a drafts folder and would have raised the same IntegrityError. The slug is
for a human tracing an evidence locator back to a file; it is not what makes the identifier
unique.

The cost is that the hash in an identifier is no longer a prefix of the document's own
`sha256`. Nothing read it that way, and the alternative was leaving a collision class open
that the composite key would keep turning into a crash.

The optional-content-hash form of `document_id` is gone. It existed only as the pre-D32
shape, and an identifier that omits either half is one that overwrites somebody's text.

### What did not change

`diff_snapshots` does not key on document identifiers, and Phase 3's acceptance is measured
on the two drafts ingested separately, where every relative path and therefore every
identifier is unchanged. `propose_structure` keys on relative paths and never on identifiers.

*Reversible* in the same sense D32 is, and with the same caveat: identifiers minted under the
older schemes remain valid and resolvable, stores keep whatever they already hold, and the
first re-ingest under this one adds rows rather than destroying any. Reverting would
reintroduce a crash on any corpus containing two copies of one file.

## D41 — Declared and enacted are two edges, on two scales, from two readings

**Phase 4.3.** Reference material states relationships; narrative shows them. Both produce
edges between the same characters, and fixture **C** exists because what a corpus declares and
what it enacts disagree. Its README sets the requirement:

> A pipeline that merges the two provenance classes into one graph loses both findings.

The findings are a relationship the bible gives a whole section to and the transmissions never
show, and a pair carrying more page time than anyone while the bible does not mention them.
Four things keep the two apart, and each of them is a place the merge could have happened.

### The text is split before it is read, not after

`revision_text` and `revision_document_spans` take a `roles` argument, and the pipeline reads
narrative and reference material as two separate runs of text. Concatenating them and reading
the whole under the narrative prompt would mark a bible's claims `observed` — asserting that
the story enacted something the author only wrote down. Both queries go through one
`_revision_rows`, because offsets from one must never be applied to the other's text.

The roles themselves moved to `store.py`, beside the CHECK constraint that is their authority.
Three modules spelling `"narrative"` was three places for one to drift from what the database
accepts.

### Provenance is part of a relation's identity

`ids.relation_id` suffixes anything that is not `observed`. A pair both declared and enacted is
two edges — that is the merge the fixture forbids — and `observed` stays unsuffixed so
identifiers already written down do not move.

### The weights are different quantities and say so

An observed weight counts passages of contact: more contact is more of the same thing. An
assertion is not a quantity. A bible stating a relationship twice has repeated itself, not
doubled the relationship. The basis is `asserted_statements`, and `require_comparable` refuses
to rank or diff it against an observed weight, which is correct rather than inconvenient.

This reached the client, where it was a real defect waiting: `buildGraph` scaled every edge
against one maximum. Before this bullet no snapshot could mix bases, so nothing had ever
exercised it. A pair stated once beside a pair sharing a hundred scenes would have rendered at
a hundredth the width — a claim nobody made. Widths are now measured against the heaviest edge
*sharing a basis*. The existing controls needed no change: **2.5** already withholds the
weight filter when bases disagree, and **2.1** already refuses to print a weight without one.

### The type is the content of the claim

The bible does not say two characters interacted; it says they are estranged siblings.
`Relation` gained `types`, unioned across every statement supporting the edge, because **4.4**
compares a declaration against an enactment and would otherwise be comparing a declaration
with its content discarded. Observed relations carry no types — counting contact is not naming
it, and an empty list would imply the question had been asked.

### What is deliberately *not* separate

**Characters resolve once, across both passes.** "Ada" in the bible and "Ada" on the page must
become one character or every relation reads as both undeclared and unenacted, and the overlay
compares nothing. `resolve` was split into `resolve_mentions`, which takes surface forms from
any number of readings; `_gather` never needed a whole `Extraction`.

**The grouping is one implementation.** `aggregate_claims` serves both passes. What differs is
passed in — provenance, weight basis, the noun in a warning — while the parts that must not
drift are the fiddly ones: how a passage is keyed when its quotation could not be located, how
context is captured, which document an offset falls in. A second copy of those is a second
place for evidence to be attributed to the wrong document.

**The verification gate is one gate.** Invariant 3 does not soften because a relation was
declared rather than enacted: a bible quotation the bible does not contain is exactly as
unusable as an invented line of dialogue. `verify` already worked on anything carrying a
quotation; only its type hints said otherwise, and they now name a `Claim` protocol.

### Nothing is hardcoded about what reference material looks like

`prompts/assert.md` describes what a document *does*, never what one is called. It refuses
relations with things rather than people, refuses names the document declines to commit to —
fixture C names Berthold and says outright it is unresolved whether Berthold is a person, a
station, or a ruling — and states that an invented relationship is worse than a missed one,
because an invention appears on the graph as something the author declared.

### Run parameters record the second reading only when there was one

`assertion_prompt_version` and `asserted_weight_basis` are added to a run's parameters only
when reference material was actually read. A run's identity is hashed from these, so adding
them unconditionally would give every narrative-only corpus a new run identifier for a
question it was never asked.

*Reversible.* Removing the reference pass leaves observed analysis byte-identical: a corpus
with no reference documents makes the same model calls, records the same parameters, and
produces the same run identifier as before this bullet.

## D42 — The overlay asks whether a pair appears on each side, and nothing about how much

**Phase 4.4.** **4.3** separated what reference material declares from what narrative enacts.
This is the view they were separated for, and fixture **C** names what it must surface:

> **Declared but never enacted.** The bible states that Ada Mbeki and Tomas Reiner are
> estranged siblings — a relationship given a whole section. They never share a scene.
>
> **Enacted but never declared.** Ada and Sister Yeong carry the most page time of any pair
> in the corpus, and the bible does not mention the relationship at all.

Run against the real fixture, the panel reads *2 declared but never enacted, 1 enacted but
never declared; 0 agreed* — Ada and Tomas, Ada and the Quorum, Ada and Sister Yeong.

### Pairs are matched by endpoints, never by relation id

**4.3** made the identifiers differ by provenance precisely so the two classes could not
merge upstream. Keying this comparison on id would therefore find agreement nowhere and
report an entire corpus as both undeclared and unenacted — the same failure the separation
was built to prevent, arriving one layer later. Matching is on the sorted endpoint pair, so a
pair also matches when the two readings named it in opposite orders.

### Nothing compares the weights

An observed weight counts passages of contact; an asserted weight counts statements.
`require_comparable` refuses to put them on one scale, and this view honours that rather than
routing around it. The question is only whether a pair appears on each side. *Declared more
strongly than enacted* is not a sentence this data can support, so it is not one the view
offers, and there is a test asserting a pair stated once and enacted four hundred times is
simply `agreed`.

### The view is withheld unless both classes are present

In a narrative-only corpus every relation is trivially enacted-but-never-declared, which
restates that the corpus has no bible rather than finding anything in it. Offering the control
anyway would be **2.5**'s failure again: something that looks like information and is not. The
refusal names *which* half is missing, because "unavailable" alone reads as a defect rather
than as a fact about the corpus.

### A hand-entered relation is neither, and is counted saying so

Invariant 5 has three provenances. A `human` relation is not evidence of the corpus
disagreeing with itself, so it takes no side — but dropping it silently would make the totals
here disagree with the totals everywhere else in the application. It is excluded from the
comparison and reported as excluded.

### Two consequences of 4.3 that only became visible on screen

**A mixed-basis snapshot stopped being an error.** The client raised a red banner reading
*this snapshot mixes weight bases; edge widths are not comparable*. Since 4.3 that is the
ordinary state of any corpus with reference material, and since the per-basis width change it
is no longer even true. A banner for the expected case teaches a reader to ignore banners, so
it is gone; `FilterControls` already withholds the weight filter and names the basis, which is
where this belongs.

**The thickness note was quietly wrong.** It read *measured against the heaviest relation in
the snapshot (3)* while asserted edges were being measured against 1. It now names each basis
and what it was measured against, whenever there is more than one.

### The marks live where they can be tested

**3.4** shipped its overlay with the marks computed inside the component, and they were
silently never applied — the effect building the graph did not depend on the diff. `applyMarks`
therefore sits in `declared.ts` beside the comparison it applies, so a test can hold the two
together, and the effect's dependency list carries the toggle. Verified live as well as in
tests: the three edges of the fixture C snapshot carry `declared-only`, `declared-only` and
`enacted-only`, and toggling the view off clears them.

A diff overlay and a provenance overlay are never drawn at once. They answer different
questions with different palettes, and one picture carrying both would answer neither.

*Reversible.* The comparison derives from a loaded snapshot and stores nothing. Deleting
`declared.ts` and its three call sites returns the client to 4.3's behaviour.

## D43 — A character's appearances are derived from snapshots, not recorded beside them

**Phase 4.5.** The registry was already collection-scoped, and the scoping already worked:
`_resolve_collection` puts a second work into the collection a project already holds, and
`resolution` matches surface forms against everything that collection knows. Probed before
building anything, *Chief Mbeki* in a second novel already resolved to the *Ada Mbeki* the
first one registered, and both snapshots already carried one `char:ada-mbeki`.

Two things were missing, and they are what this bullet is.

**Nothing tested it.** A shared universe silently becoming two casts that happen to rhyme is
the kind of failure that shows up as a graph looking slightly thin, months later. The load-
bearing test is now explicit: a character introduced under one name, referred to in the next
work by another, coming back as one person with one identifier.

**Nothing could ask where a character appears** — the obvious question about a registry that
spans works, and the only one that makes the spanning visible.

### Derived, not stored

A `character_works` table would be a second source of truth needing to be kept in step with
the snapshots it summarises, and the symptom of it falling behind is a character reported in
a work they were cut from. Snapshots are immutable (Invariant 4), so deriving from them is
stable: the same store always yields the same answer and no write path can forget to update
it. The cost is a read over each work's newest snapshot, which is arithmetic over data already
in memory.

### Only the newest reading of each work counts

A character in the first draft of a novel and cut from the second does not appear in that
novel. A registry reading every snapshot ever taken would go on asserting they do, and would
grow monotonically more wrong the more a work was revised. The snapshot each appearance rests
on is named, so the claim can be checked rather than taken — the same reason every proposal in
`structure` carries its basis.

Two states that look identical from outside are told apart rather than conflated. A character
in no current snapshot stays in the registry with no appearances, because some reading put
them there and dropping them would quietly narrow the cast the next resolution matches
against. A work nobody has analysed is named as unanalysed, because a character missing for
that reason looks exactly like a character who is not in it.

### Ordering answers the question the registry is opened with

Whoever spans most works comes first, then by name. A reader of a shared-universe registry is
looking for who carries across it, which is a different question from the alphabet.

### Two surfaces, both offline

`GET /api/registry` and `dramatis characters` (with `--spanning` and `--json`). Neither calls a
model or reaches a network, per Invariant 6 — there is a test that builds the whole registry
with a provider that would raise on any call.

### What this bullet deliberately does not include

**No browser view.** The roadmap asks 4.4 for an *overlay view* and asks 4.5 for a *registry*,
and the difference in wording is taken as meant. Editing the registry from the client is
**5.3**'s bullet — manual merge and split, with the decision recorded — and building a
read-only version of that surface now would be work done twice. The endpoint exists for
whenever the client wants it.

*Reversible.* `registry.py` reads and writes nothing; deleting it and its two call sites
returns the project to 4.4, with the cross-work behaviour it always had and the tests that
now describe it.

## D44 — A local provider must be checkably local, and must not pretend to knobs it has not got

**Phase 4.6.** Ollama runs a model on the user's own computer, which makes the phase's
acceptance sentence reachable: *a full analysis completes against a local model with the
machine offline*. That sentence is now a test — the whole pipeline through a transport that
raises if it is ever pointed anywhere but loopback.

### No SDK, and no new dependency

Ollama speaks JSON over HTTP and the standard library posts JSON over HTTP. A provider whose
entire purpose is that nothing leaves the machine, and which first required a package be
fetched from the internet, would be a joke at its own expense. The smaller transport is also
the more auditable one: Invariant 7 is a claim about where bytes go, and `urllib` in one
function is a claim a reviewer can finish checking.

The `ollama` extra therefore does not exist. There is nothing to install.

### The host is checked, and the answer is said out loud

`OLLAMA_HOST` can point anywhere, and a remote Ollama is a perfectly reasonable thing to run
— but it means the manuscript leaves the machine, which is the one promise this provider
exists to keep. `is_local` answers it and `analyse` prints a note when the answer is no.

Not an error: refusing to run against a model on a machine down the hall would be presuming
about somebody's network. But not silent either, because the alternative is a user relying on
a promise that an environment variable they set months ago has quietly withdrawn.

### Effort is not honoured, and the provider says so rather than dropping it

Anthropic takes a reasoning-effort dial; Ollama has none. Three options existed, and two are
worse than they look. *Silently ignoring it* lets two runs record different configurations
while making byte-identical calls — the mirror of the fault **D35** found in
`resolution_prompt_version`, where a run recorded what happened to it rather than what it was
asked to do. *Inventing a field* the server ignores is the same lie with more steps.

So `honours_effort` is False, `analyse` reports it when an effort was given, and the run
parameters go on recording the effort because they record what a run was **asked** to do and a
provider ignoring a setting does not unmake the choice. There is a test that two efforts
produce the same request body, and one that no `effort` key appears anywhere in it.

### Translating the vocabulary rather than passing it through

Ollama's `done_reason` says `length`; `ModelResponse.truncated` reads `max_tokens`. Left
untranslated, a reply cut off by the token budget arrives at `response.json()` as *the model
emitted malformed JSON*, which sends the reader to the prompt rather than to the budget that
actually ran out — the exact failure `ModelResponse.json` was written to prevent.

Ollama has no refusal signal, so `refused` is never true here. A local model that declines
returns prose, which fails validation as it should, rather than being mistaken for an empty
reading.

### A 404 is decided on the status, not on the wording

A missing model is the overwhelmingly common first-run failure — somebody installs Ollama and
has not pulled anything — and `ollama pull` is the whole of the fix. The first version matched
on the body text and a 404 with an unreadable body fell through to a generic message, which a
test caught. The status alone now decides it: `/api/chat` is answered whenever Ollama is
running at all, so a 404 from it is about the model.

### What the fakes could not catch, and a real server found in a minute

The adapter was written from Ollama's API contract, and the commit said so. Ollama was then
installed on the development machine, and the first thing it found was a bug the fakes were
structurally incapable of catching: **`available()` posted to `/api/tags`, which is GET-only.**
Ollama answers 405, so the adapter reported a perfectly healthy server as absent.

The harm was not cosmetic. `available()` is what gates the live test, so the live test would
have skipped itself forever on exactly the machines able to run it — green, and never
executed. The fake transports took `(url, payload, timeout)` and never modelled the verb, so
no test could have failed. `Transport` now carries the method, and four tests pin it, including
one asserting that a running server with **no models installed** still reads as available —
the state that exposed it.

Two things designed blind turned out right: a real 404 from a machine with no models produced
exactly the intended `ollama pull` sentence, and every field of a real reply mapped as
expected.

`models()` was added alongside, so the live test runs against whatever the machine actually
has rather than a name compiled in. A live test that fails because the developer pulled a
different model is a live test people switch off.

### What a real local run showed about the gate

A full `dramatis analyse --provider ollama` on a short scene completed in 45 seconds on a
four-core laptop with no GPU, and produced three characters and **no relations**: the model
returned the quotation `You are late.` where the source reads `You are late,`. One character
of punctuation, silently corrected, and **Invariant 3 refused it**.

That is the gate doing its job rather than a defect, and it is worth recording as the shape of
local analysis on small models: they paraphrase, `verify` rejects, and past
`DEFAULT_MAX_REJECTION_RATE` the whole extraction is refused rather than a thin graph shipped.
The same scene verified on other attempts, so the behaviour is a coin-flip rather than a wall.
A local model worth analysing a manuscript with wants hardware this laptop has not got; what
this machine proves is the adapter, which is what it was installed for.

### What was not built

**No cassette support specific to Ollama**, because none is needed: `CheckpointProvider` wraps
any provider. The `live` marker, whose description said *billable* and *needs a credential*,
now covers both kinds of real provider — a hosted one needing a key and a local one needing a
running server.

*Reversible.* The provider is additive and reached only through `--provider ollama`; deleting
the module and the flag leaves Anthropic exactly as it was.

## D45 — A container image, and the Postgres store split out of it

**Phase 4.7.** The bullet read *"Docker image; Postgres as an alternative store."* Those are
two deliverables of very different size, and bundling them would have produced a commit whose
Postgres half could not be honestly tested here.

### Why the split

A Docker image is self-contained: a Dockerfile, a `.dockerignore`, and a structural test.
A Postgres backend is not. `store.py` is 835 lines with ninety `?` placeholders that Postgres
spells `%s`, a `PRAGMA` and a `sqlite3.Row` factory, and — the real work — three queries that
order by `created_at, rowid` to break ties stably. That `rowid` tie-break was added by **3.2**
and **3.4** to fix real bugs: without it a diff could run backwards and report every
strengthening as a weakening. Postgres has no `rowid`, so honouring those decisions means an
explicit monotonic column and a migration at `STORE_VERSION`.

And it cannot be tested against a mock. **4.6** had just finished teaching this: an adapter
written to a contract rather than to a running server shipped a bug that a fake could not
catch. Writing a Postgres backend against no Postgres would repeat that a bullet later. So
Postgres became **4.10**, to be done against a real server — appended rather than renumbered,
because **4.8** and **4.9** are cited by number here and the governance test checks those
references.

### Three stages, so the runtime carries none of its toolchain

Node and `tsc` build the client and stay in the web stage; the source tree and build backend
build a wheel and stay in the wheel stage; the runtime is a slim Python image with a wheel and
a folder of static files. The result is 194 MB and contains no compiler. The wheel carries the
prompts and the schema as package data — verified by unzipping it before trusting it — so a
clean install has everything `analyse` needs.

### The client location is configuration, not a constant

`server.py` computed the client path relative to its own source file: correct in a checkout,
wrong for a wheel in `site-packages`, three directories below a `web/dist` that is not there.
Left alone, the image would have served the "not built" 503 for a client it was holding. The
constant became `web_root()`, reading `DRAMATIS_WEB_ROOT`, defaulting to the old path — the
same reasoning as `OLLAMA_HOST`: a path right in one deployment is wrong in another, and the
deployment is what knows. The image sets the variable to where it copied the client.

### The container binds `0.0.0.0`; `serve` still binds loopback

Inside a container, loopback is unreachable from the host, so an image keeping the
`127.0.0.1` default would never answer. Only the image overrides it. The `serve` default is
unchanged, because a manuscript should not reach the LAN because somebody ran a command, and
the boundary moves to the user's `docker run -p`: publish to `127.0.0.1` to stay private, or
to `0.0.0.0` knowingly.

### The find only a real build could surface: a name collision on PyPI

The first image built, and its container exited 127: `dramatis: executable file not found`.
`pip show` inside it read **version 0.1.1**, which this project has never produced. There is an
unrelated package named `dramatis` on PyPI, and installing by name — even with `--find-links`
pointing at our wheel — let pip prefer the stranger's higher version number and install it
instead. The image had been shipping someone else's code.

The fix installs the wheel by file path, `"$(ls /tmp/wheels/*.whl)[serve]"`, which leaves pip
no name to resolve for the application while still pulling the `[serve]` extra's dependencies
from the index. After it, the container serves version `0.1.0.dev0` — ours. A structural test
now fails if the Dockerfile reintroduces the install-by-name form.

### Verified for real, not only structurally

The proof of a Dockerfile is a build and a run, and both were done. The image builds; the
container starts healthy; `/api/health` reports the mounted store present; `/api/works` returns
a work ingested on the host through the mounted volume; and `/` serves the client's
`<title>Dramatis</title>` rather than the 503. `tests/test_docker.py` reads the file and holds
its load-bearing properties between such runs, in the manner `test_ci_workflow.py` guards CI.
A CI job that builds the image on every push was considered and left for later: it needs Docker
in the runner and adds minutes, and the structural test plus a recorded manual build is the
same gate this project already accepts for its other infrastructure config.

*Reversible.* The image, its ignore file, and its test are additive; `web_root()` defaults to
the previous behaviour when the variable is unset. Deleting all of it returns the project to a
checkout-only server with no change to how it runs from source.

## D46 — The server learns to write, and one middleware guards every write there will ever be

**Phase 4.8.** The server had been read-only by absence of write endpoints, never by
mechanism (**D31** established this). This adds its first three: creating a store, writing
settings, and saving a structure map — the metadata surface **4.9** will compose into browser
project creation. None calls a model or touches the author's text, so Invariants 6 and 7 are
untouched; the only real consequence is the guard.

### The guard is a middleware keyed on method, not a check on each endpoint

A page open on any site can `fetch` a POST at `127.0.0.1` from the user's browser. It cannot
read the reply — the same-origin policy stops that — but a write's side effect lands anyway,
and a preflight does not always intervene: a form-style POST is a "simple" request the browser
sends without asking first. What a browser cannot forge is the `Origin` header, which it stamps
with the page's true origin. So the guard compares `Origin` to the `Host` the request was sent
to and refuses a mismatch.

It lives in one middleware, guarding by HTTP method, rather than as a dependency on each
handler. That is a deliberate improvement on the bullet's literal "every one of them checks":
**D31**'s reason for settling this now was *"rather than retrofitted once there are a dozen"*,
and a middleware keyed on the method carries that further than a per-endpoint dependency could.
A write added later — **5.1**'s review status, a correction — is guarded the moment it exists,
because it is a POST or a PUT, and nobody has to remember to opt it in. The failure mode of the
per-endpoint approach is a future endpoint that forgets the guard; the method-keyed middleware
has no such mode.

A request with no `Origin` is allowed: a non-browser client such as curl or the CLI, not a
cross-site vector, because a browser cannot suppress the header on a cross-origin write. Reads
carry no guard at all — they change nothing, and the browser already refuses to hand a
cross-origin reply back to the page that asked.

### A footgun worth recording: FastAPI could not see the `Request`

The guard was first written as a dependency, `def same_origin(request: Request)`, and every
guarded call answered **422**: `{"loc": ["query", "request"], "msg": "Field required"}`.
FastAPI had taken `request` for a query parameter.

The cause is a three-part trap. `server.py` carries `from __future__ import annotations`, so
every annotation is a *string*. FastAPI resolves that string against the function's module
globals. But the framework is an optional dependency imported *inside* `create_app` (Invariant
6 forbids importing it at module load), so `Request` was a local of `create_app`, invisible to
the module globals FastAPI consults — and an unresolved annotation falls through to "query
parameter". The tell was that even the 422-expecting body-validation tests passed, for the
wrong reason, which is exactly how a broken guard would have shipped looking green.

The middleware sidesteps it entirely: middleware takes the request as a positional argument
with no annotation to resolve. The rewrite was better on its own merits and immune to the trap
that produced it.

### Verified against a live server, not only the test client

The header logic is exercised through Starlette's TestClient, but the guard's whole purpose is
what a real browser and a real ASGI stack do, so `dramatis serve` was run and driven with curl,
which can set `Origin` and `Host` freely. A same-origin PUT wrote a setting (200); a
cross-origin PUT was refused (403) **and reading the setting back proved the write never
landed** — the evil request tried to flip it and did not; a no-Origin PUT wrote (200). This is
the property the endpoint tests assert, confirmed where it actually matters.

`serve --help` and the module docstring stop calling the server read-only; both now say it
accepts writes to project metadata from the local client only.

*Reversible.* The endpoints and the middleware are additive. Removing them returns the server
to reads alone, which is where **4.7** left it.

## D47 — An excluded region is dropped, not described to the model; and 4.9 splits to build it first

**Phase 4.11, split from 4.9.** Building the browser project-creation flow surfaced that its
whole point — *"a preface excluded there produces a cast free of the people it discusses"* —
rested on machinery that did not exist. Regions had been recorded since **4.1**/**4.2** and
consumed only for CLI display; nothing in ingest or the pipeline acted on one. So 4.9 became
two bullets: **4.11**, the backend that makes an excluded region actually reduce the cast,
built first; then 4.9, the browser flow on top. Taken out of order for the same reason **3.6**
was — a later number is a dependency of an earlier one, and the roadmap says so.

### Drop the text, do not ask the model to ignore it

The alternative considered was to send the whole document and instruct the extraction prompt
to skip the preface. It was rejected, and the reasoning is worth keeping because it recurs.

Extraction reads in windows, so *either* approach must first work out which windows fall in
the excluded region — that alignment is shared. After it, the choice is only whether a
pure-preface window is *dropped* or *sent with an "ignore" instruction*. Dropping wins on
every axis that matters here: it costs nothing to send, it is deterministic, and it does not
depend on a model obeying a negative instruction across a window boundary. Instructing would
make exclusion a model *behaviour* — a preface character present in one run and absent in the
next from identical settings, which is the exact class of non-determinism **D35** and **D40**
fought, and it cuts against **4.3**'s settled principle that *the text is split before it is
read, not after.*

So exclusion is mechanical and happens at **ingest**: the kept text is stored, the preface is
not, and everything downstream — segmentation, extraction, evidence, the passage reader —
works unchanged because it reads the stored document, which is now the narrative.

Ingest was the natural home for a second reason found by looking: the work does not record the
folder it came from, so the pipeline cannot recover a revision's structure map at analysis
time, while ingest already looks the map up to apply document roles (**4.2**). Analysis-time
exclusion would have needed new plumbing and a normalised-vs-raw offset mapping; ingest-time
needed neither.

### The boundary is a quotation matched in raw text, never a stored offset

The kept span is the narrative region, located by its verbatim boundary quotations. The offset
the map records is in *normalised* space and is only ever the hint; `_locate_raw` builds a
pattern from the quotation where each run of whitespace matches any run, and searches the raw
document text, returning a raw offset in the same coordinate space as the text it cuts. This
is the discipline `text` states of every offset in the project, and it is why fixture **A**'s
preface excludes cleanly even though the file is hard-wrapped — and why a boundary quotation
carrying a word the file does not (a comma the 1894 text omits after *"good fortune"*) is
correctly *not* matched. A confirmed exclusion whose boundary cannot be found is **refused**,
not ignored: silently keeping the preface would produce the very cast the exclusion was for.

### `excluded` is a region role, and costs no schema change

A region may carry role `excluded`, alongside the two document roles. It lives only in the
structure map's JSON; the `documents.role` column keeps its two-value CHECK, because an
excluded region is not a kind of document but a span of one. A model never proposes it —
throwing text away is a person's call.

### A single file is now a corpus of one

`propose_structure` accepted only a folder. **4.9** offers a file, a folder, or a tree as
equals, and the preface that most needs excluding — a public-domain novel with a critical
introduction bound in, which is fixture **A** exactly — arrives as one file. A file is now its
own structure-map root, holding one document named for it, so the map, confirmation and
exclusion machinery reaches it unchanged.

### Proven on fixture A, no browser and no model

The mechanism is proven where **D31** measured the problem: the 34,289-character preface of
*Pride and Prejudice* is confirmed excluded, the file is ingested, and the stored document
begins at *"It is a truth universally acknowledged"* with Coleridge and Whitman — two of the
38 phantom characters — gone, the novel itself intact. Neutering the exclusion helper fails
that test and seven others, so it bites.

*Reversible.* The kept-text helper and the `excluded` role are additive; a document with no
excluded region is stored exactly as before, and removing the machinery returns ingest to
storing whole files.

## D48 — A project is created in the browser, and two bugs only a browser could find

**Phase 4.9.** Choose a file, a folder or a tree; say what each document is; mark front matter
to leave out; ingest. It calls no model — proposing reads the folder, and analysing stays
`analyse`'s job — so opening the flow costs nothing and abandoning it costs nothing.

This completes phase 4's acceptance sentence: *a project is created from the browser without
touching the command line, and a preface excluded there produces a cast free of the people it
discusses.* It rests on **4.8**'s write endpoints and **4.11**'s exclusion, both built for it.

### Two endpoints, on the split the guard already draws

`GET /api/structure/propose` reads a path and proposes what it holds. A read, so **4.8**'s
middleware leaves it alone, and it deliberately works **without a store**: creating the project
is a later step of the same flow, and 404ing the first screen would make the flow impossible to
start. `POST /api/ingest` is the write that ends it, guarded automatically by being a POST —
the property the method-keyed middleware was chosen for.

The server reads a filesystem path the browser names rather than accepting an upload. For a
local-first tool that is the honest shape: the server is on the user's machine, `ingest_file`
and `ingest_folder` already read paths, and an upload path would be a second way in to keep in
step with the first.

### The client emits the JSON the store already speaks

`create.plansFor` builds exactly what `structure.as_json` writes and `ingest.kept_text` reads —
an excluded region is a region with role `excluded` beside a narrative region carrying the
boundary quotation, not a browser-only spelling of "skip this bit". A test in each language
holds that shape, and a server test drives the whole flow over HTTP, because only a test
crossing both can catch them drifting apart. Drifting apart means a confirmed preface silently
staying in the analysis, which is the failure this whole feature exists to prevent.

Logic lives in `create.ts` rather than the component, for the reason **4.4** learned: a rule
that cannot be tested is a rule that ships broken and looks green.

### Two bugs found by running it, not by reading it

**`serve` refused to start without a project.** `resolve_store(...).require()` raises when the
file is absent, so on a fresh machine the server would not run — and if the server will not
run, there is no browser in which to create anything. The acceptance is unreachable by
construction. A store *named* on the command line may now be absent (the browser will create
it); an *unnamed* missing store still raises, because that is somebody in the wrong directory
rather than somebody starting something new. The server says which case it is.

**The client crashed to a blank page on an empty project.** `/api/snapshots` answers 404 before
the store exists, the loader read the body as an array regardless, and `found.find` threw on
`{detail: ...}` — killing the whole app. That is precisely the state a new user starts in.
Snapshot loading now tolerates a project that holds nothing, and an empty project *opens* the
creation flow, because a fresh install otherwise lands on an empty graph with no visible next
step.

Neither is reachable from a unit test of either side. Both were found within a minute of
pointing a browser at a real server with no project in it — the same lesson **4.6** and **4.7**
each recorded, arriving a third time.

### Verified as a person would do it

A real server on an empty store, a real browser, a real file with a preface. The flow opened by
itself, read the file, took a role and the line the narrative begins at, and created the
project. The store afterwards holds one work, `collectives_are_actors: false`, and 144
characters of text beginning *"It is a truth universally acknowledged"* — with Coleridge, who
is in the preface and not in the novel, absent.

### What is deliberately not here

**Naming the store in the browser.** The server serves one store, fixed when it starts, so a
name chosen in the browser would name a file the server is not serving. `dramatis serve --store
my-novel.sqlite` names it; the browser creates it. Changing that means a server that can switch
stores, which is a larger change than this bullet.

**Prompt selection**, as the bullet says: it belongs to **7.4**–**7.5**, where prompts become
versioned artefacts, and offering it earlier would let a project record a prompt nothing can
compare against.

*Reversible.* The two endpoints and the panel are additive. The `serve` change widens what is
allowed and refuses nothing that used to work.

## D49 — One set of queries, two databases, and the column that replaces `rowid`

**Phase 4.10.** Postgres as an alternative store, chosen by pointing `--store` at a URL
instead of a file. SQLite remains the default and the shape the project is designed around —
one file you can archive, send or deposit. Postgres exists for the deployment that is wrong
for: several people reading one corpus, a container with no persistent disk, an institution
that backs up databases and not directories.

### A driver behind the interface, not a second Store

`Store` is unchanged, and so are its queries. A `Connection` wrapper rewrites what differs on
the way to the driver, which is why nothing above `dramatis.drivers` knows there are two
backends and why no call site had to be touched. The alternatives were worse in the same way:
a second Store means every future method written twice, and a query builder means writing
something other than SQL forever. Both drift.

Scoping it found less to do than feared. The SQL was already portable — `ON CONFLICT ... DO
UPDATE` is in both, and no `INSERT OR REPLACE`, `AUTOINCREMENT` or SQLite date function
appears anywhere. Three things actually differ, and they are the whole module: placeholders,
the tie-break column, and the foreign-key pragma.

### `rowid` becomes `seq`, and only where ordering depends on it

**3.2** and **3.4** each fixed a real bug by ordering on `created_at, rowid`: a revision or
snapshot identifier is a content hash, so ordering by it puts two rows written in the same
second into an order decided by hashing — and a diff run backwards reports every strengthening
as a weakening. `rowid` is SQLite's own. Postgres gets an explicit `BIGSERIAL seq` on the three
ordered tables, and queries write `{tiebreak}` for whichever the dialect uses.

Only those three tables get it. A column existing purely to break ties is noise on a table
nothing orders. The two schemas therefore differ by one column, deliberately: a store is
chosen once, not moved between backends, and no claim is made that a SQLite file can be poured
into Postgres.

The property is tested rather than asserted — three revisions written with identical
timestamps come back in insertion order, and the test also states what the identifier-sorted
order would have been, so a regression shows the bug 3.2 fixed rather than a bare inequality.

### Three bugs a real Postgres found, and a mock would not have

The bullet said *tested against a real Postgres, never a mock*, and this is what that bought.

**`SELECT *` fed the driver's own column into a dataclass.** `TextRevision(**row)` refused
`seq` as an unexpected keyword. Fixed at the driver: bookkeeping columns are stripped from
every row on the way out, so `SELECT *` keeps working everywhere and nothing above knows the
column exists. SQLite rows pass through untouched, because `sqlite3.Row` supports positional
access that callers use and `rowid` is never selected anyway.

**`fetchone()[0]` is not a thing a Postgres row does.** `count()` took its result
positionally; psycopg returns a mapping, which raises `KeyError: 0`. The column is now named.

**A URL is not a path.** `--store` was `type=Path` in argparse and `resolve_store` called
`Path()` on it, so a Postgres URL arrived as `postgresql:\dramatis:...` and was reported as a
missing project. Both now pass a URL through as the string it is. Without this the backend
worked and was unreachable from the command line, which is the same as not working.

### The tests run rather than skipping themselves

They are in the ordinary suite and skip only when no server answers, rather than sitting
behind a marker that is always deselected. **4.6** recorded what the other shape costs: a live
test that skips itself forever reports green on exactly the machines that could have run it.
A container is cheap and reproducible in a way a billable API is not, so there is no reason to
hide these. The module docstring gives the one `docker run` line that makes them run.

### Verified end to end

Not only unit tests: the whole pipeline — ingest, extraction, resolution, aggregation,
snapshot — against a live Postgres, producing a snapshot that still validates against the
schema; snapshot immutability enforced on both backends; and a full CLI round trip,
`dramatis ingest` then `dramatis status`, against a URL.

*Reversible.* SQLite behaviour is unchanged and untouched by the driver: the placeholder is
already `?`, the tie-break is already `rowid`, and nothing is stripped from its rows. Deleting
`drivers.py` and inlining the SQLite driver returns the store to where 4.9 left it.

---

## D50 — Review is recorded beside the snapshot, keyed to the claim rather than to the document

**Phase 5.1.** Every node and every edge now carries a review status — `proposed`,
`accepted`, `corrected`, `rejected` — settable from the browser, from the command line, and
over the API. The vocabulary is not new: the schema has enumerated these four since **0.3**
and the registry has had a column for them since **1.5**. What was missing was any way to set
one, and any answer to the question of where a decision lives when the thing it is about is
immutable.

### Beside the snapshot, never in it

A snapshot is immutable (Invariant 4) and a review happens after one was written, so recording
a decision inside the stored document would mean rewriting an artifact something may already
cite. Decisions live in their own table and are read back *over* the document by
`review.overlay`. `GET /api/snapshots/{id}` goes on serving exactly what was archived, and
`GET /api/snapshots/{id}/reviews` answers the separate question. Two requests rather than one
merged reply, for the reason the server has given since **1.9**: a second representation of
one graph is a second place for the truth to live.

The consequence in the client is that the detail panel's old `Review` row had to go. It read
the status straight out of the document, which from this bullet onwards is stale the moment
somebody rules on the claim. It is replaced by a control — the one thing on that panel a
person *does* rather than reads — so there is one place showing where review stands instead
of two disagreeing.

### Keyed by work and subject, not by snapshot

A decision is about a claim, not about the document that happened to carry it. Identifiers are
derived from content and names rather than minted per run (**1.4**), so the same character is
the same character in the next reading of the same work. A decision scoped to a snapshot would
expire every time the analysis re-ran, and asking somebody to re-accept a cast they have
already been through is how a review tool stops being used.

This is the seam **5.2** needs, and deliberately not **5.2** itself: what is asserted here is
only that a decision is not scoped to one document. Carrying human work *into a re-analysis* —
so a new snapshot is built already knowing what was accepted — is that bullet's, and nothing
here writes a status into a snapshot being built.

The snapshot the decision was taken in is recorded beside it, because what was on the screen is
part of what was decided, and a reviewer looking at an old ruling is entitled to know which
reading produced the claim they ruled on.

### Append-only, because "never silently overwritten" starts with never losing anything

Each decision is a row and the newest stands. A status column updated in place would lose that
somebody once accepted what has since been rejected — and phase 5's promise is precisely that
human judgements are not quietly discarded. `dramatis review --history` exists so that
promise is checkable rather than merely made.

Restating the standing decision is a no-op rather than a second identical row, so a client
that re-sends on every render does not fill the log with restatements. Restating it *with a
different note* is a new decision: somebody has given their reason.

### `corrected` must say what it corrects

The one rule here that the bullet does not state. A correction with no note is
indistinguishable from a rejection somebody softened, and until **5.2** makes a correction an
actual change to the graph, the note is the whole of it. Requiring it costs a reviewer one
sentence and stops the vocabulary's most informative term from being its emptiest. `rejected`
needs no note — "this is not a real character" is complete on its own.

### The guard needed nothing

`POST /api/snapshots/{id}/reviews` is the first write that is about the graph rather than
about project metadata, and **4.8**'s middleware refused a cross-origin one before the
endpoint existed, because it is keyed on the method. That was the argument for settling the
guard at the first write rather than retrofitting it; a test now asserts the property on a
write built a phase later.

### Three copies of one vocabulary, held together by a test

The four statuses are written in the published schema, in the store's `CHECK` constraint, and
in `review.STATUSES` — and the client repeats them a fourth time. A constraint that has
drifted from the vocabulary rejects a status the application holds to be valid, and the
failure surfaces as a database error in front of a user. `TestTheVocabulary` reads the enum
out of the schema and the constraint out of the DDL and asserts all three agree.

### What is deliberately not here

**The manual.** `docs/manual.pdf` is built from HTML by a Chrome render, and editing the
prose without rebuilding the PDF would leave the two disagreeing — worse than leaving both
alone. The manual gets its pass at **6.6**, where documentation is the bullet.

**Bulk review.** Accepting a whole cast at once is the obvious next convenience and the
obvious way to make a review meaningless. If it arrives it should be a considered decision,
not a side effect of this one.

*Reversible.* Everything is additive: one table, one module, one endpoint pair, one CLI verb,
one client module. Dropping `reviews` returns the project to where 4.11 left it, since nothing
else reads it and no stored snapshot changed shape.

---

## D51 — A correction is applied when a snapshot is built, and the reading it overrules is written down

**Phase 5.2.** **5.1** let a person say *this is wrong*. This lets them say *what it should
be*, and makes the answer stick across re-analysis. A correction replaces one field of one
node or edge, is recorded beside the reading it was made on, and is written into every
snapshot built afterwards.

### Applied at build time, not to the snapshot on screen

Snapshots are immutable (Invariant 4), so a correction made against snapshot *n* cannot change
*n*. It is applied in `pipeline.analyse`, between `build_document` and `save_snapshot`, so
*n+1* is rendered with it. That ordering is deliberate in a second way: the corrected document
is what the schema validates, so a correction that would produce an invalid graph fails at the
run that introduced it rather than being found later by a reader.

The cost is that correcting a name does not rename the node on screen. The alternative —
having the client apply corrections over the archived document before drawing it — was
considered and declined. It would mean the browser rendering a graph that no stored document
says, and it would have to be replicated in TypeScript, which is the one piece of logic that
must not have two implementations. What the panel does instead is show the correction beside
the claim, with what it replaces, and say plainly that it applies at the next analysis. The
same line disappears once a reading carries it — a distinction the first browser run got
wrong and which `appliedIn` now decides.

### The reading is overruled, and the overruling is recorded

Every correction stores `was`: what the reading said at the moment it was made. When a later
analysis proposes something different, the person's value still stands — but the run's
competing claim is written to `correction_conflicts` and reported in the run's warnings, in
`dramatis correct`, and in the panel.

This is the second half of *never silently overwritten*, and it cuts both ways. Without the
correction winning, re-analysis discards a person's work. Without the conflict being recorded,
re-analysis discards the model's finding — the same failure with the roles swapped. A
correction that is merely *applied* where the reading still agrees raises nothing, because a
report that fires on every correction is a report nobody reads by the tenth one.

A correction whose subject a later reading does not contain at all is reported and **not**
resurrected. Putting the character back would invent a node with no evidence behind it, which
Invariant 3 exists to prevent; what is owed is telling somebody their work has nothing left to
attach to.

### Corrected is `human`, with a consequence that is intended

Invariant 5 defines `human` as *entered or corrected in the app*, so a corrected node or edge
carries that provenance and there was nothing to decide. The consequence is real: a corrected
relation leaves **4.4**'s declared-against-enacted comparison. It is not lost — 4.4 already
counts a third bucket and says "n relation(s) were entered by hand and are neither declared nor
enacted" — but a corrected edge stops being evidence about the corpus disagreeing with itself,
which is right. A person's edit is not a finding about the text. The relation's original
provenance is not stored a second time: the previous snapshot is immutable and still says it.

### Only what a person can actually judge

Correctable: a character's `name`, `kind`, `aliases`, `notes`; a relation's `types`,
`valence`, `directed`, `notes`. Refused, each with its reason rather than "unknown field":

- **`weight` and `weight_basis`** — a weight is a count on a declared basis, not an opinion.
  Correcting the types or the tone says what somebody means without claiming a tally the
  evidence does not show.
- **`evidence`** — verified against the source text or not stored at all (Invariant 3), so it
  cannot be typed in.
- **`id`, `source`, `target`** — identity, which is **5.3**'s merge and split.
- **`provenance`, `review_status`** — set by the act of correcting, and by **5.1**.
- **`confidence`, `salience`** — a reading's estimate of itself, on a scale a person's
  judgement is not on.

The reasons are the point, and they survive all the way to the command line: `--field` has
deliberately no `choices` list, because argparse would replace every one of those explanations
with "invalid choice", which is the single answer that teaches nothing.

### One row per field, and 5.1's rule relaxed

Corrections are keyed by (work, subject, **field**). Correcting a name and correcting a note
are two decisions, and one row per subject would let the second silently discard the first —
the failure this bullet is named after, committed against itself.

Recording a correction also sets the subject's review status to `corrected`, because the two
are one act. That status previously demanded a note; it now accepts a recorded correction as
the explanation instead, which is what **5.1** was asking for and could not yet have. What is
still refused is the empty claim, not the missing sentence.

### Values keep their types, in three places

`types` is a list, `valence` is a number, `directed` is a boolean — and a shell, a text box
and a JSON column all naturally flatten them to strings. The CLI parses per field, the client
parses per field, and the store writes JSON rather than text. A value flattened anywhere would
reach the schema as the wrong type and be rejected there, where the message names a JSON
pointer instead of the mistake.

### Verified on a real corpus, with a re-analysis that cost nothing

The 1,125-test suite runs against SQLite and a live Postgres. Beyond that: a correction was
made in the browser against the full *Pride and Prejudice* snapshot — removing `madam`, `you`
and the misspelled `Lizzie` from Elizabeth Bennet's aliases — and the novel was then
re-analysed in full, replayed from the existing checkpoint at 63 served calls and **zero live
ones**, which is what made an end-to-end acceptance test of a 240-relation corpus free. The
new snapshot carries the corrected alias list with `provenance: human` and
`review_status: corrected`; the old one is byte-identical to what it was.

### What is deliberately not here

**Correcting the registry.** A corrected name changes what the snapshot says, not what the
collection's registry holds, so resolution still matches the old canonical form next time.
Renaming in the registry would apply the correction to every work in the collection, which is
not what was asked for and is close to **5.3**'s territory.

**Bulk correction**, and **the manual**, which stays as it is until its next rebuild.

*Reversible.* Two tables, one module, one endpoint pair, one CLI verb, one client module, and
four lines in `analyse`. Dropping `corrections` returns snapshots to being rendered exactly as
5.1 left them.

---

## D52 — Merging and splitting are one shape, and the registry is the whole mechanism

**Phase 5.3.** `resolution` has said since **1.5** that it cannot merge two characters the
registry already knows, and why: *merging is destructive and cannot be reviewed after the
fact, so it stays a human act*. This is that act, in both directions.

### One shape, two directions

A merge and a split are the same operation — surface forms moving from one character to
another. A merge moves all of a character's forms and retires it; a split moves some of them
to a new character and leaves the source standing. Holding them as one shape is not tidiness:
it is what makes a split the undo of a merge, which is the only undo either has, and it is why
one `registry_decisions` table records both.

### Nothing is applied, because nothing needs to be

**5.2** had to write corrections into a document as it was built. This needs no equivalent.
Once the registry says a form denotes the surviving character, the next reading resolves it
there, and aggregation groups edges by character — so the graph comes out merged on its own,
with the two characters' edges to a third combining and their weights adding. That is what the
bullet means by *recorded in the registry*: the record **is** the mechanism, not a note beside
one.

The proof is that no module outside `identity.py` and the store learned anything. In
particular `diff` did not: **3.4** already recognises a merge because *the surviving character
now lists the absorbed one's name among its own surface forms, which is exactly what the
registry writes down when it merges two*. That sentence was written a phase and a half before
anything could merge. A test now runs the whole loop — merge, re-analyse, diff — and gets
`MERGED` back, so the anticipation and the implementation are held together rather than
assumed to agree.

### Retired, not deleted

A merged-away character keeps its row, loses every claim including the one on its own name,
and points at the character that absorbed it. Deleting it would be tidier and wrong: snapshots
already written name that identifier, and a reader following one back is owed better than
nothing. It leaves `list_characters` by default so nothing can resolve to it and no registry
reader is shown somebody with no part in the work; `include_retired=True` brings it back for
whoever is tracing.

### Human work follows the character, or this bullet would undo the last two

Reviews (**5.1**) and corrections (**5.2**) are recorded against an identifier. Merging would
strand everything recorded against the absorbed one — a correction reported as unappliable on
every run for ever, and a rejection that silently stops applying. So `current_reviews` and
`current_corrections` read through `merged_into`: a ruling made before a merge goes on applying
to the character that survived it, and where both characters had been ruled on, the later
ruling stands, which is already the rule for two rulings on one subject.

Chains are followed to the end — merge B into A, then A into C, and B answers to C — because
stopping at the first hop would lose the work on the second merge instead of the first.

Edges follow too. Merging one endpoint changes which pair an edge joins and therefore its
identifier, so a correction to `rel:bram--miss-ada` would be stranded by a merge at either end.
`ids.relation_endpoints` reads an identifier back into the endpoints it was built from — the
inverse of `relation_id`, living in the module that owns the `--` join, so no caller has to
treat that convention as a rule.

The redirect is folded into the store rather than into each reader, for the reason the origin
guard is a middleware: a caller that has to remember is a caller that will forget. The raw logs
are never rewritten; what moves is the answer to *where does this stand now*, which is the only
question a merge changes.

### A merge is `corrected`, not `human`

**5.2** made a corrected node `human`, because Invariant 5 defines `human` as *entered or
corrected in the app* and a correction replaces what a reading claimed. A merge does not: the
character is still enacted by the narrative, and what a person settled is who it is. So the
survivor's review status becomes `corrected` and its provenance is left alone — a merged
character stays in **4.4**'s declared-against-enacted comparison, where a human-provenance one
would drop out for a reason that is about naming rather than evidence.

A **split** is the other way round: it puts a node in the graph that no reading proposed, so
the character it creates is `human`.

### Two human decisions that can disagree

A standing correction to a character's `aliases` (**5.2**) replaces the whole list when a
snapshot is built — including the names a merge has just handed over, and with them the record
`diff` reads to recognise the merge. The merge reports this and proceeds. Picking a winner
silently between two decisions a person made is the one thing this phase exists not to do, and
refusing the merge over an unrelated earlier edit would be worse than saying what will happen.

### What is refused

- **Merging a character into itself**, or into one already retired.
- **A split that moves every form.** That is a rename, not a split: there would be no second
  person, only the same one under a new identifier, and the registry would have lost the
  identity that makes two snapshots comparable.
- **A split naming the new character something the collection already claims**, which would
  make one form denote two people — the thing `character_aliases`' primary key exists to make
  unstorable.

### No browser view, and why

`/api/registry` has been served since **4.5** and no client has ever read it; there is no
registry view to add a merge button to, and building one is its own piece of work rather than a
corner of this bullet. Merge and split are available on the command line and over the API,
which is exactly how the registry has been reachable since it existed. It is also the right
pace for the operation: a merge is the most consequential write in the application — the only
one a later analysis *acts on* — and typing two identifiers is not the friction to be sorry
about.

### Verified

1,178 tests against SQLite and a live Postgres, including the whole loop on the pipeline: two
characters that are one person, merged, re-analysed, and the resulting graph holding one
character whose edge to a third carries both passages. `rewrite_characters` is exercised on
both backends because handing a form from one character to another has to land whole — the
alias primary key refuses it half-way, which is what a partial move would be.

And on the real corpus, which is where the bug below was found. *Miss Bennet* — whom the run
registered separately from *Jane Bennet*, though in Austen the eldest Miss Bennet is Jane —
merged, and the novel re-analysed from its checkpoint at zero live model calls. Her four edges
folded into Jane's (Bingley 27+4, Elizabeth 40+2, Mrs Bennet 14+1, Wickham 0+1), the cast went
from 102 to 101, and `diff` reported one merge and **nothing removed**.

### The bug only an existing project file could find

`CREATE TABLE IF NOT EXISTS` adds tables and never columns, and `merged_into` is the first
column this schema has ever added to a table that already existed. Every store made before
5.3 therefore failed on the first query naming it — which is every read of the registry. The
store's own docstring claimed the DDL was enough to bring an older file up to date; it was
true of tables and false of columns, and had never been tested because no column had ever been
added.

`ADDED_COLUMNS` now carries additive columns and `Store.open` applies any that are missing.
The column list is asked of the cursor's `description` rather than of `PRAGMA table_info` or
`information_schema`, because the DB-API has one and the dialects do not agree on the others.
A test asserts every column in `ADDED_COLUMNS` is also in the DDL, so one can never be added
for new stores and forgotten for old ones; another opens a store built without the column;
and the Postgres suite drops the column and reopens.

### What is deliberately not here

**Undo of a merge as its own verb** — a split does it, which is why they share a shape.
**Renaming in the registry**, which **5.2** also left alone. **The manual**, until its next
rebuild.

*Reversible.* One module, one table, one column, two CLI verbs, two endpoints. Nothing outside
`identity.py` decides anything: dropping it and the column returns the registry to where 5.2
left it.

---

## D53 — Three findings a re-analysis cannot produce, and the granularity each is checked at

**Phase 5.4.** Everything before this reports what a reading found. This reports what the
corpus no longer agrees with itself about — three things that survive a re-analysis because
re-analysing cannot see them.

**A cast with a stale name in it looks exactly like a cast.** Re-read the work after renaming
somebody in one chapter and missing another, and the graph comes back with both names in it,
confidently, as two people or as one depending on how the model felt. Nothing in the pipeline
is wrong; the corpus is.

**A locator with nowhere to land fails one citation at a time**, and only when somebody opens
it. **2.4** re-anchors the quotation where it can; what it cannot do is tell you how many
claims are pointing at a place the work no longer has.

**Two copies of a chapter are an ordinary corpus** to a reader that was not told they are the
same chapter. Every interaction in it is read twice and weighted double, and every number
downstream is quietly wrong.

### The comparison is between documents, and that is a decision

The stale-name check asks whether a name the last reading found in a document is no longer
written *there* and still written in *another*. Document granularity is chosen because that is
the shape the mistake has: a rename is a find-and-replace in the file being worked on, and what
it misses is another file.

A finer grain would report every paragraph a name dropped out of during an ordinary rewrite,
which is most paragraphs. The cost is real and stated rather than hidden: a single-document
work can never produce this finding, because there is no elsewhere for a name to be stale in.

The same choice is what removes the need for a stop-list. A form only qualifies where it
vanished from a document *entirely*, and `you`, `her mother` and `my dear` — all real aliases
in the *Pride and Prejudice* registry — are in every document before and after, so they are
never candidates. No vocabulary is encoded, which Invariant 1 and the "not tied to one author's
method" non-goal both require.

A name that disappears from the *whole* work is not reported either. That is a clean removal,
and reporting it would be reporting an edit rather than an inconsistency — the report would cry
wolf on every draft and be turned off.

### A rename is not asserted, because it cannot be proven

The finding is *this name left here and is still written there*, with every remaining location.
It does not claim what replaced it. Where another surface form of the same character appeared
in exactly the documents this one left, `replaced_by` names it — and the registry is what makes
those two forms one person, so that is evidence rather than a guess. Where no such form exists,
the field is absent. An unevidenced rename is worse than an unexplained pair, which is the rule
`diff` already follows for merges.

### Superseded documents are checked whichever revision is being looked at

The other two findings compare a reading against a later text and are empty when nothing has
moved. This one is not: a document read alongside the document that revises it is wrong in the
revision that holds them both, not in the comparison between two. So it is reported even for a
project nobody has revised, which is exactly when it is most useful — before the first
analysis has spent anything.

### Saying that nothing was compared

A report on a reading of the current text has no findings, and so does a report on a corpus
with nothing wrong. Those are different answers and the report gives different ones: *the
reading is of the current text* against *nothing stale, nothing lost, nothing read twice*. An
empty report that means "there was nothing to compare" is the same failure **2.5** recorded —
a control that looks like information and is not.

### It reports and does not repair

Every one of these has more than one right answer. A stale name might want the rename finished,
or might be a deliberate archaism in a character's speech; a lost position might want the
evidence re-anchored or the claim dropped; a superseded document might want removing from the
corpus or might be there on purpose. Choosing is the author's, and a tool that chose would be
editing the work, which the non-goals forbid.

### Two defects the report found by being read

**A name shown with its spaces eaten.** `normalise_whitespace` strips both ends — right for
matching a quotation, wrong for showing one, and it rendered *Sister Yeong keeps* as
`Sister[Yeong]keeps`. The context now keeps the space beside the name where the text had one
and loses it where the window cut mid-word.

**A fixture's README is part of the fixture.** Ingesting `fixtures/c` whole puts its own
README into the corpus, and the README names the characters — so the first run of the
acceptance test found a third stale location, in documentation. The check was right and the
corpus was wrong; the test now ingests the corpus without the files that describe it.

### No browser view, again

Like **5.3**, this is CLI and API. The report is most useful in the minutes after re-ingesting
a revised draft and before spending anything on re-analysing it, and the browser has no
ingest-a-new-revision flow to hang it beside — **4.9** creates projects, not revisions. Adding
one is its own bullet, not a corner of this one.

### Verified

1,201 tests against SQLite and a live Postgres, and the acceptance sentence tested against
fixture **C** itself rather than a stand-in: *Yeong* renamed to *Sarto* through all three
transmissions while the character bible goes on saying *Yeong*, and the report naming both
remaining occurrences with the words around them. Run live on the same fixture from the command
line, which is where both defects above turned up.

*Reversible.* One module, one store method, one CLI verb, one endpoint. Nothing else changed:
`continuity.py` reads what the store already holds and writes nothing.

---

## D54 — Confidence is drawn where it was recorded, and its absence is said out loud

**Phase 5.5.** The schema has carried `confidence` on nodes and edges since **0.3** and nothing
has ever looked at it. This draws it: an edge a reading was less than half sure of is dotted,
and the sidebar says how many of them there are.

### The finding that shapes the whole bullet: nothing records it

Dramatis's own pipeline has never asked a model how sure it was. The extraction response
schema has no `confidence` field, `aggregation.Relation.as_schema` emits none, and
`_character_as_schema` emits none — so every graph this application has produced, including the
241-relation reading of *Pride and Prejudice*, carries confidence on nothing at all.

That is worth stating plainly rather than working around, and it is why the bullet is smaller
than it looks. **5.5 asks for confidence to be surfaced, not for it to be produced.** Producing
it means changing the extraction prompt, which changes `prompt_sha256`, which makes every
existing snapshot incomparable with every new one — correctly, by **D35**, but that is a
deliberate act belonging to **Phase 7**, where the prompt becomes the object of study rather
than an input. Slipping it into a UI bullet would spend the project's whole back catalogue of
comparability on a rendering change.

So what ships is the rendering, and it is not dead: `confidence` is part of a *published*
schema (Invariant 8), so a document produced by another tool — or imported by **6.3** — may
carry it, and a hand-authored fixture in this repository already does.

### An absent confidence is not a low one

The rule everything else rests on. `detail.ts` has drawn this distinction for panel fields
since **2.1** — *a snapshot that records no confidence is not a snapshot with low confidence* —
and the same mistake made in ink, across a whole graph, would be far worse: it cannot be
argued with, and it would tell every existing Dramatis user that their entire graph is
uncertain. An element the reading said nothing about is drawn exactly as it is drawn today.

A value outside 0–1 is treated as unsaid rather than clamped, for the same reason: clamping
turns a malformed document into a claim nobody made.

### Low is below the midpoint, because the midpoint is the only number available

The schema declares confidence as a value from 0 to 1 and says nothing about what it counts.
There is no `confidence_basis` the way there is a `weight_basis`, so any threshold is a reading
of an undeclared scale. 0.5 is the one that needs no tuning: the point at which a reading stops
being more sure than not. Below, not at — at exactly 0.5 a reading is as sure as not, and the
mark is for the edges it was more unsure than sure of.

The number is printed on screen rather than applied silently, because a reader who disagrees
with it needs to know it was applied at all. If confidence ever acquires a declared basis, this
becomes a decision with evidence behind it and should move then.

### Dotted, because dashed is spoken for twice

The diff draws a removed edge dashed and **4.4** draws a declared-but-never-enacted edge
dashed, both for the reason recorded there: *a dashed edge is the one convention a reader
already reads as "not really there"*. A third meaning on that mark would make all three
unreadable, so confidence takes dotted.

**An overlay outranks it.** A diff and a provenance comparison each answer a question the
reader has just asked; confidence is a standing property of the graph. The stylesheet arranges
this by order — the `uncertain` rules sit before the overlay rules — rather than by anything in
the logic, and both overlay paths *append* their class rather than replacing, so the mark
survives underneath and reappears when the overlay is dismissed. Checked live: an edge carrying
both renders in the overlay's dashed colour, and its `uncertain` class is still there
afterwards.

### Saying "not recorded" is the feature, on today's data

For every graph Dramatis has produced, the sidebar's answer is *not recorded by this reading* —
and that row is always shown. Leaving it out where there was nothing to report would let a
reader take an unqualified graph for a confident one, which is the question they are most
likely to be asking of it. The legend explaining the dotted mark is the opposite case and is
withheld unless something on screen carries the mark: a legend for an unused encoding is the
control **2.5** refused, one that looks like information and is not.

### Nodes as well as edges

The bullet names edges. Characters carry `confidence` in the same schema, under the same rule,
and a convention that applies to half a graph is one a reader cannot trust — so an uncertain
character takes a dotted border. It is the same rule, not a second one.

### What is deliberately not here

**A confidence filter.** The bullet asks for the uncertain to be *distinct*, not hidden, and
hiding them is the one thing a reader studying reliability should not be offered by default.

**Asking the model for it**, as above: Phase 7's, with the prompt hash it costs.

**A derived confidence** — from evidence counts, verification rejections, or anchor similarity.
Each is arithmetic the pipeline already has, and each would be a number with no declared basis
put on a scale that has none: exactly the mistake `weight_basis` exists to prevent, and the
project has refused it twice already.

### Verified

310 web tests, `tsc`, prettier, and the 1,201-test Python suite untouched and green. Driven in
a real browser twice: against the *Pride and Prejudice* reading, where the row reads *not
recorded by this reading*, no legend is offered and no edge is marked; and against a snapshot
built for the demo carrying confidence on 160 of 241 relations, where 69 render dotted at 0.55
opacity, the row reads *160 of 241 relation(s), 69 below 0.50*, and the panel's number agrees
with the mark.

*Reversible.* One client module, two stylesheet rules, two lines of sidebar. Nothing in the
store, the pipeline or the schema changed.

---

## D55 — Dramatis publishes as `dramatis-personae`

**Before phase 6.** The distribution name changes; the import package and the console command
do not.

`dramatis` on PyPI belongs to an actor library for Ruby and Python, last released on 6 June
2008 at version 0.1.1, alpha, two sdists and no wheels. It is unrelated in every way except
the word.

This is not a theoretical clash. **D45** records the first Docker image exiting `127` with
`dramatis: executable file not found`, because pip preferred the stranger's 0.1.1 over this
project's 0.1.0.dev0 and installed it instead: the image was shipping somebody else's code.
That was fixed by installing the wheel by file path, which left the collision itself in place —
and with it `pip install dramatis`, **6.5**'s citable release and **6.7**'s installers.

### Only the distribution name moves

`import dramatis` and the `dramatis` command are resolved by the interpreter and the console
script table, never through the index, so neither is at risk and neither changes. Renaming them
would alter what every module imports and what every user types, to solve a problem neither
has. The precedent is ordinary: `pillow` imports as `PIL`, `beautifulsoup4` as `bs4`.

A governance test pins all three — the distribution name, the command, and the wheel's package
— and asserts that nothing installs the application *by* name from an index, in either
spelling. The Dockerfile's file-path install stays, because installing by the new name would
work today and silently break on the day somebody registers it.

Verified by building the wheel: `dramatis_personae-0.1.0.dev0-py3-none-any.whl`, importing as
`dramatis`, carrying the five prompts and the two schema files as package data, and exposing
`dramatis = dramatis.cli:main`.

### PEP 541 is worth filing and not worth waiting for

The abandonment criteria are met on paper — no release in eighteen years — but they are written
for somebody *continuing* an abandoned project: a request must show "improvements made on their
fork" and "why creating a fork under a different name isn't viable", and neither is true here.
PyPI does grant these, at its discretion and on no timetable. So the request should be filed
and the release should not wait for it. If the name is ever granted, publishing under both
costs nothing.

*Reversible.* One line of `pyproject.toml`. Nothing imports it.

---

## D56 — Invariant 7 admits a named corpus source, and phase 4 reopens for one

**Before phase 6.** Every corpus this application has ever seen was on a local disk. Most of
the corpora it is wanted for are not: they are Google Docs in Drive folders, and the only way
to analyse them today is to export the lot by hand, which is a chore that has to be repeated
on every revision and which quietly breaks the one thing revisions are for.

### The invariant had to move first, and that was not mine to decide

Invariant 7 read *"No egress except to the user's chosen provider"*, and reading a Drive folder
is egress to Google. The spirit survives the change easily — a manuscript already kept in
somebody's Drive is not newly exposed by being read back out of it — but the letter did not,
and the roadmap says invariants "are not negotiable and are not re-litigated per phase". So the
amendment was put to the owner rather than assumed, and it is narrow by construction:

> …and to a source the user has named as holding their corpus.

with three conditions written into the invariant itself. A named source is **read-only**; it is
contacted **only while ingesting**; and it is **never contacted unless a person named it in
that run**. Nothing becomes reachable by default, and no stored project reaches anything.

**Invariant 6 is untouched, and that is the load-bearing part.** `documents` stores `content`,
deliberately — *"a path on somebody's laptop is not a durable reference"* — so a Drive document
is copied into the store at ingest and every later operation reads the store. Analysing,
diffing, opening a passage, running a continuity report and exporting all stay offline exactly
as they are today. The network appears in one place and disappears again.

### Why this is phase 4 reopening rather than a new phase

Phase 4 is *Heterogeneous corpora*, and it is the phase about where a corpus comes from and how
its shape is inferred and confirmed. A Drive folder is a heterogeneous corpus that happens not
to be local. Renumbering 6 and 7 to make room for a new phase would break the cross-references
in a dozen decision entries — **D45** alone points at 7.4–7.5 — and Phase 4 has grown bullets
mid-flight twice already (**4.10** split from 4.7, **4.11** from 4.9). So it gains **4.12**–
**4.15** and one more sentence of acceptance.

### The shape the code is already in

The seam turned out to be one line wide. `ingest_folder` reduces a directory to a list of
`(relative path, text)` pairs plus a root string for the structure map, and *everything*
downstream — content hashing, document identity, revisions, structure maps, region exclusion —
is defined on that list. So a source is an interface with two questions, the filesystem is one
implementation, and Drive is another. **4.12** is that refactor and changes no behaviour, which
is precisely why it is its own bullet: it is the one that must not be entangled with a network
client.

Two details fall out rather than needing decisions. Google Docs export as **Markdown**, which
preserves the headings structure inference reads and lands on `.md`, already in
`TEXT_SUFFIXES`. And **D32**'s document identity — path plus content hash — works untouched if
the hash is taken over the exported text, so an edited Doc becomes a new document in a new
revision exactly as an edited file does.

### Authentication: an installed-app OAuth flow

Chosen over a service account, which would mean re-sharing every folder with a robot address,
and over a pasted access token, which expires hourly. The user brings a client secret once and
consents in a browser; the refresh token is cached **outside the project file**, because a
project store is a thing people send to each other and a credential must not travel in one.
Read-only scope.

### What was ruled out

**Google Drive for Desktop**, which looks like it should make this unnecessary and does not:
Google Docs appear there as `.gdoc` files containing a URL rather than the text. The workaround
this bullet exists to avoid is not actually available.

*Reversible.* The invariant amendment is additive and the bullets are unbuilt; deleting both
returns the project to a local-disk-only tool.

"""Proposing what a folder of documents actually is.

A structure map answers three questions about each document — is this narrative or reference
material, how is it addressed, and is it a revision of something else — and it answers them
as *proposals*, each carrying the evidence it rests on.

The module has two halves, and the seam between them is the point. `propose_structure` reads
the folder and answers only what a folder can evidence. `propose_with_model` reads the
documents and answers the rest. Then `confirm` settles what a person has taken responsibility
for, `save` stores it, and `restore` puts it back on the next reading so nobody is asked
twice.

**Nothing here reads a filing convention.** Fixture **C** is built to catch exactly that: its
reference documents live in `series-bible/`, its narrative in `transmissions/`, its revisions
in YAML frontmatter, and its units are numbered `t01`. Every one of those is a convention
somebody chose, and its README says so — *"If any of this leaks into the core as a special
case, Invariant 1 or the 'not tied to one author's method' non-goal has been broken."* So a
directory called `series-bible` earns no inference here, and neither does one called
`draft-2`.

What is left is evidence that does not depend on anybody's habits:

**Two documents with the same filename in different places are probably the same document
twice.** That is how fixture **B**'s drafts relate, and it is recoverable without knowing
that its author happened to name the folders `draft-1` and `draft-2`. How alike the two are
is measured and reported but never used as a gate, because a chapter rewritten from nothing
is still that chapter. Fixture **C** has no such pairs and correctly yields none: `t01` and
`t02` share neither name nor content, and calling them revisions would be an invention.

**A document's addressing is what can actually be reproduced.** Today that is the blank-line
default and nothing else, for the reason **D27** gives.

**Role is not proposed from the folder at all.** Whether a document is narrative or reference
is a question about what it says, and answering it from a folder name would be exactly the
special case the fixture forbids. `propose_structure` leaves it open, with the reason
recorded; `propose_with_model` answers it by reading the document, and a person settles it.
An honest *unknown* is worth more than a guess that happens to be right on the two corpora
somebody tested, which is why `unsure` survives all the way to `unknown` and why an unknown
role cannot be confirmed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from importlib.resources import files
from pathlib import Path
from typing import Any

from dramatis.providers import ModelRequest, Provider, ProviderError
from dramatis.reanchor import Anchor, reanchor
from dramatis.segmentation import DEFAULT_SEGMENT_TYPE
from dramatis.sources import Reading, Source, as_source
from dramatis.store import NARRATIVE, REFERENCE, utc_now
from dramatis.text import normalise_whitespace

PROMPT_PACKAGE = "dramatis.prompts"

REWRITE_SIMILARITY = 0.5
"""Below this, a revision is reported as a rewrite rather than an edit.

Reported, not enforced. Measured on fixture **B**, with `autojunk` off — see `_similarity`:

```
chapter-01.md   draft-1 vs draft-2   1.000   untouched
chapter-02.md   draft-1 vs draft-2   1.000   untouched
chapter-03.md   draft-1 vs draft-2   0.838   the rewritten one

cast.md      vs draft-1/chapter-01   0.119   unrelated
chapter-01   vs chapter-02           0.222   unrelated
fixture C, t01 vs t02                0.317   unrelated
```

Revisions and unrelated documents do separate cleanly, so a gate would work on this corpus.
It is still not used as one. A revision is a revision because somebody revised it, and a
chapter thrown away and written again from nothing would score like an unrelated file while
being the revision a reader most needs recorded. The filename carries the claim; this number
tells whoever confirms it whether they are looking at an edit, a rewrite, or a coincidence.
"""

UNKNOWN = "unknown"
"""The two roles a document or a region can have, and the absence of an answer.

`unknown` is a first-class value rather than a null, because the difference between "nobody
has looked at this yet" and "this was looked at and is reference material" decides whether a
person is asked about it. Collapsing them loses the only documents worth asking about.
"""


class StructureError(Exception):
    """A structure map could not be proposed or saved. The message names the document."""


@dataclass(frozen=True)
class Proposal:
    """One answer, and what it rests on.

    The basis travels with the value because a structure map is a thing somebody is asked to
    confirm, and *confirm this* is not a question anybody can answer without being told what
    the proposal was made from.
    """

    value: str | None
    basis: str
    settled: bool = False
    """True when this is a fact rather than a guess — something read off the corpus rather
    than inferred from resemblance."""


@dataclass(frozen=True)
class Region:
    """A stretch of one document that may be classified separately from the rest.

    Front matter, a critical preface, an appendix bound into the same file: **D31** widened
    the structure map to hold these, because the commonest shape a public-domain text arrives
    in is one file containing a preface and a novel, and per-document classification cannot
    reach inside it.
    """

    label: str
    role: Proposal
    starts_at: int = 0
    ends_at: int | None = None
    """Character offsets into the document's *whitespace-normalised* text, or None for "to
    the end". Normalised because that is the only text a quotation is ever anchored in
    (`text.find_quotation`), and a boundary measured against one text and applied to the
    other lands in the wrong place by however much the source was hard-wrapped."""

    begins_with: str = ""
    ends_with: str = ""
    """The verbatim quotations this region was found to start and stop at, if any.

    The offsets are the hint and these are the authority, which is what `text` says of every
    offset in this project. It matters more here than elsewhere: a structure map is saved and
    reused on later ingests (**4.2**), by which time the author may have edited the document
    and moved every offset in it. The quotations still find the boundary; the numbers do not.
    """


@dataclass(frozen=True)
class DocumentPlan:
    path: str
    characters: int
    """Length of the whitespace-normalised text, which is what `Region` offsets index."""

    role: Proposal
    addressing: Proposal
    revision_of: Proposal
    regions: tuple[Region, ...] = ()


@dataclass(frozen=True)
class StructureMap:
    root: str
    documents: tuple[DocumentPlan, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def revisions(self) -> tuple[tuple[str, str], ...]:
        """(later, earlier) for every document proposed as a revision of another."""
        return tuple(
            (plan.path, plan.revision_of.value) for plan in self.documents if plan.revision_of.value
        )

    def plan_for(self, path: str) -> DocumentPlan | None:
        return next((plan for plan in self.documents if plan.path == path), None)


def _similarity(first: str, second: str) -> float:
    """How alike two documents are, on their normalised text.

    **`autojunk=False`, and the flag is the whole of this function.** Difflib's autojunk
    heuristic treats any element appearing in more than 1% of a sequence longer than 200 as
    noise. On a character sequence that is every common letter, which makes the ratio both
    meaningless and asymmetric: fixture B's rewritten chapter measured 0.054 one way round,
    0.545 the other, and 0.838 with the heuristic off. The first of those nearly became a
    documented finding about revisions being indistinguishable from unrelated files.

    Measured in full rather than short-circuited on `quick_ratio` for the same reason: this
    number is shown to somebody as a measurement, and an upper bound is not one.
    """
    return SequenceMatcher(
        None, normalise_whitespace(first), normalise_whitespace(second), autojunk=False
    ).ratio()


def _propose_revision(path: str, text: str, others: dict[str, str], order: list[str]) -> Proposal:
    """Whether this document appears to be a revision of another.

    **The filename carries the claim.** Two files called `chapter-03.md` in sibling folders
    are the same chapter, and they remain the same chapter when the text has been rewritten,
    because that is what revising means. Similarity would separate fixture B's revisions from
    its unrelated pairs — see `REWRITE_SIMILARITY` — but a chapter thrown away and written
    again would score like a stranger while being the revision that matters most.

    The similarity is measured anyway and reported, so somebody confirming a proposal can see
    whether they are agreeing to an edit or to a rewrite — or to a coincidence, in the case of
    two unrelated files that happen to share a name.

    The *earlier* by folder order is proposed as the original. That is a guess about
    direction, stated as one: a folder listing carries no timestamps worth trusting.
    """
    name = Path(path).name
    candidates = [
        other
        for other in others
        if other != path and Path(other).name == name and order.index(other) < order.index(path)
    ]
    if not candidates:
        return Proposal(None, f"no earlier document is named {name}")

    scored = sorted(
        ((_similarity(text, others[other]), other) for other in candidates), reverse=True
    )
    best, other = scored[0]
    shape = (
        f"{best:.0%} of the text in common"
        if best >= REWRITE_SIMILARITY
        else f"only {best:.0%} of the text in common, so a rewrite rather than an edit, "
        "or two unrelated files that happen to share a name"
    )
    return Proposal(other, f"same filename as {other}, {shape}")


def _propose_addressing() -> Proposal:
    """How this document is addressed.

    Only one answer is currently reproducible, and D27 explains why: a work records the names
    of its segment types and never the rules that found them, so any division other than the
    blank-line default cannot be rebuilt to open a passage at the position a snapshot recorded.
    Proposing `chapter` here would be proposing something the rest of the system cannot honour.
    """
    return Proposal(
        DEFAULT_SEGMENT_TYPE,
        "blank-line sections, the only division this project can currently reproduce (D27)",
        settled=True,
    )


def _propose_role() -> Proposal:
    """Whether a document is narrative or reference material.

    Not answered here, and the refusal is the point. The signal a folder offers is its own
    names — `series-bible/`, `transmissions/`, `cast.md` — and every one of those is a
    convention somebody chose. Fixture C is built to punish reading them.

    Deciding this means reading what the document says, which is a model's job and 4.2's
    bullet.
    """
    return Proposal(
        UNKNOWN,
        "needs the text read, not the folder named; answer it with --ask or --set (4.2)",
    )


def _whole_document(text: str) -> Region:
    """One region covering everything, until something can say otherwise.

    A preface bound into the same file is a region (D31), and finding where it ends means
    reading the text. Proposing a single region is the honest floor: it claims only that the
    document exists, and leaves the division to the step that can see inside it.
    """
    return Region(
        label="whole document",
        role=_propose_role(),
        starts_at=0,
        ends_at=len(normalise_whitespace(text)),
    )


def propose_structure(corpus: Source | Path | str, reading: Reading | None = None) -> StructureMap:
    """Read a corpus and propose what it holds.

    Reads text only to compare documents with each other; it calls no model and reaches no
    network beyond whatever the source itself is, so a user can look at the proposal before
    deciding whether to spend anything.

    ``reading`` lets a caller that has already read the source pass it in rather than cause a
    second read — which for a folder saves a little work and for a source that costs a round
    trip saves the round trip.
    """
    source = as_source(corpus)
    if reading is None:
        reading = source.read()

    texts = reading.texts
    order = list(texts)
    documents = tuple(
        DocumentPlan(
            path=relative,
            characters=len(normalise_whitespace(text)),
            role=_propose_role(),
            addressing=_propose_addressing(),
            revision_of=_propose_revision(relative, text, texts, order),
            regions=(_whole_document(text),),
        )
        for relative, text in texts.items()
    )

    notes: list[str] = []
    if not documents:
        notes.append(f"{source.root} holds no readable text files")
    if all(plan.revision_of.value is None for plan in documents) and len(documents) > 1:
        notes.append(
            "no document appears to be a revision of another. Where revisions are recorded "
            "inside the files rather than by their names, only reading them would find it, "
            "and nothing reads them for that: the filename carries the claim."
        )

    return StructureMap(
        root=source.root, documents=documents, skipped=reading.skipped, notes=tuple(notes)
    )


def as_json(structure: StructureMap) -> dict[str, Any]:
    """The map as a document, for a caller that has to show or store it."""

    def proposal(value: Proposal) -> dict[str, Any]:
        return {"value": value.value, "basis": value.basis, "settled": value.settled}

    return {
        "root": structure.root,
        "documents": [
            {
                "path": plan.path,
                "characters": plan.characters,
                "role": proposal(plan.role),
                "addressing": proposal(plan.addressing),
                "revision_of": proposal(plan.revision_of),
                "regions": [
                    {
                        "label": region.label,
                        "starts_at": region.starts_at,
                        "ends_at": region.ends_at,
                        "begins_with": region.begins_with,
                        "ends_with": region.ends_with,
                        "role": proposal(region.role),
                    }
                    for region in plan.regions
                ],
            }
            for plan in structure.documents
        ],
        "skipped": [{"path": path, "why": why} for path, why in structure.skipped],
        "notes": list(structure.notes),
    }


# -- what the model proposes ------------------------------------------------------------


PROMPT_VERSION = "structure-v1"
"""Bumped whenever `prompts/structure.md` changes. A saved map records the version that
proposed it, so a map confirmed against an older prompt can be told apart from a fresh one."""

PROMPT_FILE = "structure.md"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["documents"],
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "role", "reason"],
                "properties": {
                    "path": {"type": "string"},
                    "role": {"enum": [NARRATIVE, REFERENCE, "unsure"]},
                    "reason": {"type": "string"},
                    "narrative_begins_with": {"type": "string"},
                    "narrative_ends_with": {"type": "string"},
                },
            },
        }
    },
}
"""`unsure` is in the enum on purpose. Constrained decoding will produce whichever values it
is offered, so a two-value enum does not get honest answers — it gets a coin flip recorded as
a classification, on exactly the documents a person most needs to look at."""


def prompt_text() -> str:
    """The structure prompt as shipped, read from inside the package."""
    return files(PROMPT_PACKAGE).joinpath(PROMPT_FILE).read_text(encoding="utf-8")


def _anchor(text: str, quotation: str) -> Anchor | None:
    """Where a boundary quotation falls in the normalised text, or nothing.

    Found with `reanchor`, the same ladder that finds an evidence quotation after the text has
    moved. Two reasons rather than one. A model asked for verbatim text will now and then
    return it re-flowed or with a word's worth of drift, which is what that module exists to
    forgive; and the saved map is re-applied to documents the author has edited since, which
    is the harder half of the same problem.
    """
    if not quotation.strip():
        return None
    return reanchor(text, normalise_whitespace(quotation))


def _regions_from(
    normalised: str, begins: str, ends: str, role: Proposal
) -> tuple[tuple[Region, ...], str | None]:
    """Divide a document at the boundaries proposed for it, or say why it was not divided.

    Returns the regions and, when a boundary was offered but could not be found, a note. The
    note is the more important half. A boundary quoted from a document it is not in means the
    reading that produced it is unreliable, and the failure has to be visible: the fallback
    keeps the whole document, so without the note the map would look like a confident answer
    that this document has no preface at all.
    """
    whole = (Region("whole document", role, 0, len(normalised)),)
    if not begins.strip() and not ends.strip():
        return whole, None

    opening = _anchor(normalised, begins)
    closing = _anchor(normalised, ends)

    if (begins.strip() and opening is None) or (ends.strip() and closing is None):
        missing = begins if opening is None else ends
        return whole, f"a narrative boundary is not in the document: {missing[:60]!r}"

    starts_at = opening.start if opening else 0
    ends_at = closing.end if closing else len(normalised)

    if ends_at <= starts_at:
        return whole, "the narrative was said to end before it begins, so nothing was divided"

    regions: list[Region] = []
    if starts_at > 0:
        regions.append(
            Region(
                "before the narrative",
                Proposal(REFERENCE, "the narrative was placed as starting after it"),
                0,
                starts_at,
            )
        )
    regions.append(
        Region("narrative", role, starts_at, ends_at, begins_with=begins, ends_with=ends)
    )
    if ends_at < len(normalised):
        regions.append(
            Region(
                "after the narrative",
                Proposal(REFERENCE, "the narrative was placed as ending before it"),
                ends_at,
                len(normalised),
            )
        )
    return tuple(regions), None


def propose_with_model(
    structure: StructureMap,
    texts: Mapping[str, str],
    provider: Provider,
    *,
    effort: str | None = "medium",
    max_tokens: int = 8192,
) -> StructureMap:
    """Ask a model what each document is, and where its narrative begins and ends.

    This fills the two questions `propose_structure` refuses on principle, because both need
    the text read rather than the folder named. The answers stay proposals: the model's own
    sentence is carried into the basis so the person confirming reads *why* before agreeing,
    and `unsure` is preserved as `unknown` rather than rounded to whichever role is commoner.

    `texts` are the documents as read from disk. Only those the structure already knows about
    are sent, so a caller cannot widen what leaves the machine by passing extra entries.
    """
    plans = {plan.path: plan for plan in structure.documents}
    sending = {path: text for path, text in texts.items() if path in plans}
    missing = [path for path in plans if path not in sending]
    if missing:
        raise StructureError(f"no text was supplied for {missing[0]}, so it cannot be read")
    if not sending:
        return structure

    request = ModelRequest(
        prompt="\n\n".join(f"--- {path} ---\n{text}" for path, text in sending.items()),
        system=prompt_text(),
        max_tokens=max_tokens,
        effort=effort,
        output_schema=RESPONSE_SCHEMA,
        metadata={"step": "structure", "prompt_version": PROMPT_VERSION},
    )

    try:
        response = provider.complete(request)
    except ProviderError as error:
        raise StructureError(f"the structure proposal failed: {error}") from error

    if response.refused:
        raise StructureError(
            "the provider declined to read the corpus. A refusal is not an empty answer - "
            "no document was classified."
        )

    try:
        payload = response.json()
    except ProviderError as error:
        raise StructureError(f"the structure proposal failed: {error}") from error

    answers = {
        entry["path"]: entry
        for entry in (payload.get("documents") or [])
        if isinstance(entry, dict) and entry.get("path") in plans
    }

    documents: list[DocumentPlan] = []
    notes = list(structure.notes)

    for path, plan in plans.items():
        entry = answers.get(path)
        if entry is None:
            notes.append(f"the model did not answer for {path}, so it is still unclassified")
            documents.append(plan)
            continue

        said = entry.get("role", "unsure")
        role = Proposal(
            UNKNOWN if said == "unsure" else said,
            f"read by {response.model}: {entry.get('reason') or 'no reason given'}",
        )
        regions, problem = _regions_from(
            normalise_whitespace(sending[path]),
            entry.get("narrative_begins_with", "") or "",
            entry.get("narrative_ends_with", "") or "",
            role,
        )
        if problem:
            notes.append(f"{path}: {problem}")

        documents.append(
            DocumentPlan(
                path=plan.path,
                characters=plan.characters,
                role=role,
                addressing=plan.addressing,
                revision_of=plan.revision_of,
                regions=regions,
            )
        )

    return StructureMap(
        root=structure.root,
        documents=tuple(documents),
        skipped=structure.skipped,
        notes=tuple(notes),
    )


# -- what the user confirms, and what is kept -------------------------------------------


CONFIRMED = "confirmed by you"
CORRECTED = "corrected by you"


def confirm(structure: StructureMap, corrections: Mapping[str, str] | None = None) -> StructureMap:
    """Apply the user's corrections and settle the map.

    A settled role is one a person has taken responsibility for, which is why this is a
    separate step from `propose_with_model` rather than a flag on it. What the model said is
    kept in the basis behind the confirmation, so a map read back a year later still shows
    where the answer came from and who agreed to it.

    **A document still `unknown` is refused rather than saved.** Saving it would mean never
    being asked again about the one document nobody has managed to classify, and it would do
    so silently, which is the failure mode this whole module is arranged against.
    """
    # What the caller typed is checked before what the map holds. A mistyped role is the
    # user's most recent action and the thing they can fix; reporting some other document's
    # missing answer first sends them looking for a fault that is not there.
    corrections = dict(corrections or {})

    absent = set(corrections) - {plan.path for plan in structure.documents}
    if absent:
        raise StructureError(
            f"no document at {sorted(absent)[0]} in {structure.root}, so there is "
            "nothing to correct there"
        )

    wrong = {path: role for path, role in corrections.items() if role not in (NARRATIVE, REFERENCE)}
    if wrong:
        path, role = next(iter(wrong.items()))
        raise StructureError(f"{path}: {role!r} is not a role; use {NARRATIVE} or {REFERENCE}")

    unknown = [
        plan.path
        for plan in structure.documents
        if corrections.get(plan.path, plan.role.value) in (UNKNOWN, None)
    ]
    if unknown:
        raise StructureError(
            f"{len(unknown)} document(s) have no role yet, starting with {unknown[0]}. "
            f"Set each to '{NARRATIVE}' or '{REFERENCE}' before confirming - a saved "
            "'unknown' would never be asked about again."
        )

    documents = []
    for plan in structure.documents:
        corrected = corrections.get(plan.path)
        if not corrected:
            basis = f"{CONFIRMED}: {plan.role.basis}"
        elif plan.role.value == UNKNOWN:
            # Nothing proposed a role here, so there is nothing to have overridden. Saying
            # "over needs the text read" would dress a refusal up as a rejected opinion.
            basis = CORRECTED
        else:
            basis = f"{CORRECTED}, over {plan.role.basis}"

        role = Proposal(corrected or plan.role.value, basis, settled=True)
        documents.append(
            DocumentPlan(
                path=plan.path,
                characters=plan.characters,
                role=role,
                addressing=plan.addressing,
                revision_of=plan.revision_of,
                regions=tuple(
                    region if region.role.value != plan.role.value else replace(region, role=role)
                    for region in plan.regions
                ),
            )
        )

    return StructureMap(
        root=structure.root,
        documents=tuple(documents),
        skipped=structure.skipped,
        notes=structure.notes,
    )


def save(structure: StructureMap, store: Any, *, when: str | None = None) -> int:
    """Write the confirmed map to a project store, and return how many documents were saved.

    Refuses an unconfirmed map. The saved answer is not asked about again, so writing a
    proposal here would turn a guess into a settled fact by the act of storing it.
    """
    unsettled = [plan.path for plan in structure.documents if not plan.role.settled]
    if unsettled:
        raise StructureError(
            f"{unsettled[0]} has not been confirmed. Only a confirmed map is saved, because a "
            "saved answer is not asked about again."
        )
    if not structure.documents:
        return 0

    payload = {entry["path"]: entry for entry in as_json(structure)["documents"]}
    store.save_structure_map(structure.root, payload, when or utc_now())
    return len(payload)


def _proposal_from(payload: Mapping[str, Any]) -> Proposal:
    return Proposal(
        payload.get("value"), payload.get("basis", ""), settled=bool(payload.get("settled"))
    )


def restore(
    structure: StructureMap, saved: Mapping[str, Any], texts: Mapping[str, str]
) -> StructureMap:
    """Put previously confirmed answers back onto a freshly read folder.

    This is what makes a map *reused* rather than merely saved. Everything measured from the
    folder — sizes, revision proposals — is taken from the fresh reading, because the folder
    is what it is now; only the answers a person gave are restored, because those are the part
    a person gave.

    Region boundaries are re-anchored rather than restored, through the same function that
    made them. A document edited since it was confirmed has moved every offset in it, and the
    quotations are what survive that. A boundary that no longer anchors is reported and the
    document falls back to one region: the role stays confirmed, the division does not, and
    the note says which.
    """
    documents: list[DocumentPlan] = []
    notes = list(structure.notes)
    restored = 0

    for plan in structure.documents:
        entry = saved.get(plan.path)
        if entry is None:
            documents.append(plan)
            continue

        restored += 1
        role = _proposal_from(entry.get("role", {}))
        regions = tuple(
            Region(
                label=region.get("label", "whole document"),
                role=_proposal_from(region.get("role", {})),
                starts_at=region.get("starts_at", 0),
                ends_at=region.get("ends_at"),
                begins_with=region.get("begins_with", "") or "",
                ends_with=region.get("ends_with", "") or "",
            )
            for region in entry.get("regions", [])
        )
        boundary = next(
            (region for region in regions if region.begins_with or region.ends_with), None
        )

        if boundary is not None and plan.path in texts:
            regions, problem = _regions_from(
                normalise_whitespace(texts[plan.path]),
                boundary.begins_with,
                boundary.ends_with,
                boundary.role,
            )
            if problem:
                notes.append(
                    f"{plan.path}: {problem} It was confirmed against an earlier version of "
                    "this document; the whole of it will be read until you divide it again."
                )
        elif not regions:
            regions = plan.regions

        documents.append(
            DocumentPlan(
                path=plan.path,
                characters=plan.characters,
                role=role,
                addressing=plan.addressing,
                revision_of=plan.revision_of,
                regions=regions,
            )
        )

    if restored:
        notes.append(f"{restored} document(s) kept the answers you confirmed earlier")
    return StructureMap(
        root=structure.root,
        documents=tuple(documents),
        skipped=structure.skipped,
        notes=tuple(notes),
    )


def structure_for(corpus: Source | Path | str, store: Any) -> StructureMap:
    """Propose a corpus's structure, with anything already confirmed for it put back.

    The reading path for every later caller: an ingest asks this, gets settled answers where
    somebody has given them and proposals where nobody has, and can tell the two apart by
    `settled`. It calls no model.
    """
    source = as_source(corpus)
    reading = source.read()
    structure = propose_structure(source, reading)
    saved = store.structure_map(structure.root)
    if not saved:
        return structure
    # The texts the source already handed over. Reading them off the source rather than
    # rebuilding paths under the root is what makes a single file work without a special
    # case — its one document is named for the file, so joining root to it would look for
    # `novel.txt/novel.txt` — and what stops a network source being read a second time.
    return restore(structure, saved, reading.texts)

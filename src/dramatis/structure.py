"""Proposing what a folder of documents actually is.

A structure map answers three questions about each document — is this narrative or reference
material, how is it addressed, and is it a revision of something else — and it answers them
as *proposals*, each carrying the evidence it rests on. **4.2** puts the proposals to a model
and then to the user, who confirms or corrects them; this module is what there is to confirm.

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

**Role is not proposed at all.** Whether a document is narrative or reference is a question
about what it says, and answering it from a folder name would be exactly the special case the
fixture forbids. It is left open, with the reason recorded, for the model and the user in
**4.2**. An honest *unknown* is worth more than a guess that happens to be right on the two
corpora somebody tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dramatis.ingest import TEXT_SUFFIXES, IngestError, read_text
from dramatis.segmentation import DEFAULT_SEGMENT_TYPE
from dramatis.text import normalise_whitespace

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
    """Character offsets into the document, or None for "to the end"."""


@dataclass(frozen=True)
class DocumentPlan:
    path: str
    characters: int
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
        "needs the text read, not the folder named; proposed by the model in 4.2",
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
        ends_at=len(text),
    )


def propose_structure(root: Path | str) -> StructureMap:
    """Read a folder and propose what it holds.

    Reads text only to compare documents with each other; it calls no model and reaches no
    network, so a user can look at the proposal before deciding whether to spend anything.
    """
    root = Path(root)
    if not root.exists():
        raise IngestError(f"no such folder: {root}")
    if not root.is_dir():
        raise IngestError(f"{root} is a file; a structure map describes a folder")

    found = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )

    texts: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []

    for path in found:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES:
            skipped.append((relative, f"not a text file ({path.suffix or 'no suffix'})"))
            continue
        try:
            texts[relative] = read_text(path)
        except IngestError as error:
            skipped.append((relative, str(error)))

    order = list(texts)
    documents = tuple(
        DocumentPlan(
            path=relative,
            characters=len(text),
            role=_propose_role(),
            addressing=_propose_addressing(),
            revision_of=_propose_revision(relative, text, texts, order),
            regions=(_whole_document(text),),
        )
        for relative, text in texts.items()
    )

    notes: list[str] = []
    if not documents:
        notes.append(f"{root} holds no readable text files")
    if all(plan.revision_of.value is None for plan in documents) and len(documents) > 1:
        notes.append(
            "no document appears to be a revision of another. Where revisions are recorded "
            "inside the files rather than by their names, only reading them will find it, "
            "which is 4.2."
        )

    return StructureMap(
        root=str(root), documents=documents, skipped=tuple(skipped), notes=tuple(notes)
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

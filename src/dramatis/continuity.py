"""What the corpus no longer agrees with itself about.

A revision changes the text under an analysis that was made of the text as it was. Most of
what that breaks is harmless and self-correcting: re-read the work and the graph catches up.
Three things are not, because re-reading cannot see them.

**A name the work has moved on from.** An author renames somebody in the chapter they are
working on and misses the two places that mention them elsewhere. Re-analysing does not
report it — it reports a cast, and a cast with a stale name in it looks exactly like a cast.
What is checkable, and checked here, is that a name the last reading found in a document is
no longer written there and *is* still written in another. That is a fact about two texts, not
a judgement, and it comes with every remaining location so the fix is a list rather than a
search.

The comparison is between *documents*, deliberately, because that is the shape the mistake
has: a rename is a find-and-replace in the file being worked on, and what it misses is another
file. A finer grain would report every paragraph a name dropped out of during an ordinary
rewrite. The cost is that a single-document work can never produce this finding — there is no
elsewhere for a name to be stale in — and that is the honest answer rather than a gap.

**A locator with nowhere to land.** Evidence records a structural position as well as a
quotation. Delete a section and the position stops existing, so a citation that used to open
the source opens nothing. **2.4** re-anchors the quotation where it can, and this reports what
that leaves: which claims are pointing at a place the work no longer has, and whether their
words survive anywhere.

**A draft that was replaced and kept.** The structure map records that one document revises
another. If a revision holds both, every scene in that chapter is being read twice, and every
interaction in it is weighted double. Nothing else notices: two copies of a chapter are a
perfectly ordinary corpus to a reader that was not told they are the same chapter.

**Nothing here calls a model, and nothing here changes anything** (Invariant 6). It is
arithmetic over two texts the store already holds, and it reports rather than repairs: every
one of these has more than one right answer, and choosing between them is the author's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from dramatis.passage import (
    PassageNotFound,
    StructureNotReproducible,
    find_passage,
    spec_for_types,
)
from dramatis.reanchor import reanchor_selector
from dramatis.segmentation import segment_text
from dramatis.store import Store
from dramatis.text import normalise_whitespace

CONTEXT_CHARACTERS = 40
"""How much text either side of an occurrence to quote, matching what evidence records."""


class ContinuityError(Exception):
    """A continuity report could not be built. The message names what is missing."""


@dataclass(frozen=True)
class Location:
    """One place a name is still written, named the way evidence names a place."""

    document_id: str
    document_path: str | None
    at: int
    """Character offset into the document's normalised text. A hint for a reader, never an
    identity: the selector below is what survives the next edit."""

    prefix: str
    exact: str
    suffix: str

    def __str__(self) -> str:
        where = self.document_path or self.document_id
        return f"{where}: ...{self.prefix}[{self.exact}]{self.suffix}..."


@dataclass(frozen=True)
class StaleName:
    """A name this revision stopped using in one document and still uses in another."""

    character_id: str
    character_name: str
    form: str
    retired_from: tuple[str, ...]
    """Documents that had this name at the last reading and no longer do."""

    replaced_by: str | None = None
    """Another form of the same character that appeared where this one went, where the
    registry can prove the two denote one person. Absent otherwise: a rename is a claim, and
    an unevidenced one is worse than an unexplained pair."""

    locations: tuple[Location, ...] = ()

    def __str__(self) -> str:
        became = f", now written {self.replaced_by!r} there" if self.replaced_by else ""
        return (
            f"{self.character_name}: {self.form!r} is gone from "
            f"{', '.join(self.retired_from)}{became}, but still appears "
            f"{len(self.locations)} time(s) elsewhere"
        )


@dataclass(frozen=True)
class LostPosition:
    """A claim pointing at a structural position this revision does not have."""

    subject_kind: str
    subject_id: str
    path: tuple[dict[str, Any], ...]
    quotation: str
    document_id: str | None = None

    words_survive: bool = False
    """Whether the quotation can still be found somewhere in the revision.

    The difference between a citation that has moved and one that is gone, and the reader
    needs it: the first is a re-anchoring, the second is a claim with nothing behind it.
    """

    def __str__(self) -> str:
        where = " › ".join(
            f"{step.get('type')} {step.get('index') or step.get('label') or ''}".strip()
            for step in self.path
        )
        fate = "the words are still in the work" if self.words_survive else "the words are gone too"
        return f"{self.subject_kind} {self.subject_id}: {where} no longer exists; {fate}"


@dataclass(frozen=True)
class SupersededDocument:
    """A document another one revises, still being read alongside it."""

    document_id: str
    path: str
    superseded_by: str

    def __str__(self) -> str:
        return (
            f"{self.path} is revised by {self.superseded_by}, and this revision holds both, "
            "so everything in that document is being read twice"
        )


@dataclass(frozen=True)
class Report:
    """Everything the corpus no longer agrees with itself about."""

    work_id: str
    snapshot_id: str
    read_revision: str
    """The revision the reading was made of."""

    against_revision: str
    """The revision it is being checked against."""

    stale_names: tuple[StaleName, ...] = ()
    lost_positions: tuple[LostPosition, ...] = ()
    superseded: tuple[SupersededDocument, ...] = ()

    notes: tuple[str, ...] = field(default_factory=tuple)
    """Checks that could not run, and why. A report silently missing a third of itself is
    worse than one that says which third."""

    @property
    def empty(self) -> bool:
        return not (self.stale_names or self.lost_positions or self.superseded)

    @property
    def unchanged(self) -> bool:
        """Whether the reading and the text being checked are the same revision.

        Not a finding and not a defect: it is what a project looks like before anybody has
        revised anything, and the report says so rather than reporting nothing.
        """
        return self.read_revision == self.against_revision

    def __len__(self) -> int:
        return len(self.stale_names) + len(self.lost_positions) + len(self.superseded)


def _occurrences(text: str, form: str) -> list[int]:
    """Where a surface form appears in a text, as offsets.

    Bounded by non-word characters rather than by ``\\b``, so a form ending in punctuation —
    ``Mr.`` — is still matched at its end. Case-insensitively, because a name at the start of
    a sentence is the same name; the forms this is asked about are ones that vanished from a
    document entirely, which no common word ever does.
    """
    if not form.strip():
        return []
    pattern = re.compile(rf"(?<!\w){re.escape(form)}(?!\w)", re.IGNORECASE)
    return [match.start() for match in pattern.finditer(text)]


def _context(raw: str, *, keep: str) -> str:
    """Collapse a run of context to one line without losing the space beside the name.

    ``normalise_whitespace`` strips both ends, which is right for matching a quotation and
    wrong for showing one: it turns "Sister Yeong keeps" into "Sister[Yeong]keeps". The edge
    the name sits against keeps its space where there was one, and loses it where the context
    was cut mid-word.
    """
    collapsed = normalise_whitespace(raw)
    if not raw or not collapsed:
        return collapsed
    if keep == "end" and raw[-1].isspace():
        return collapsed + " "
    if keep == "start" and raw[0].isspace():
        return " " + collapsed
    return collapsed


def _location(document_id: str, path: str | None, text: str, at: int, form: str) -> Location:
    ends = at + len(form)
    return Location(
        document_id=document_id,
        document_path=path,
        at=at,
        prefix=_context(text[max(0, at - CONTEXT_CHARACTERS) : at], keep="end"),
        exact=text[at:ends],
        suffix=_context(text[ends : ends + CONTEXT_CHARACTERS], keep="start"),
    )


def _documents_of(store: Store, revision_id: str) -> dict[str, Any]:
    revision = store.get_text_revision(revision_id)
    if revision is None:
        raise ContinuityError(f"no text revision {revision_id!r}")
    found = {}
    for document_id in revision.document_ids:
        document = store.get_document(document_id)
        if document is not None:
            found[document_id] = document
    return found


def _by_path(documents: dict[str, Any]) -> dict[str, Any]:
    """Documents keyed by the path they were ingested under.

    A document's identifier carries its content hash (**D32**), so the same file in two
    revisions has two identifiers and only the path says they are the same file.
    """
    return {document.path: document for document in documents.values() if document.path}


def _stale_names(
    store: Store,
    characters: Iterable[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[StaleName, ...]:
    """Names a document stopped using that the corpus still uses somewhere else.

    The rule is deliberately narrow. A form is only considered where it *completely* vanished
    from a document that had it — which is what a rename does and what no ordinary edit does
    to a common word. That is also why no stop-list is needed: ``you`` and ``her mother`` are
    in every document before and after, so they are never candidates.
    """
    before_by_path = _by_path(before)
    after_by_path = _by_path(after)
    shared = sorted(set(before_by_path) & set(after_by_path))
    if not shared:
        return ()

    findings: list[StaleName] = []

    for character in characters:
        forms = [str(character.get("name") or "")]
        forms += [str(alias) for alias in character.get("aliases") or []]
        forms = [form for form in forms if form.strip()]

        # Where each form stands in each document, on both sides. Computed once per character
        # rather than per pair, since every form is asked about every document.
        present_before = {
            form: {path for path in shared if _occurrences(before_by_path[path].content, form)}
            for form in forms
        }
        present_after = {
            form: {path for path in shared if _occurrences(after_by_path[path].content, form)}
            for form in forms
        }

        for form in forms:
            retired = sorted(present_before[form] - present_after[form])
            if not retired:
                continue

            elsewhere = sorted(present_after[form])
            if not elsewhere:
                # Gone from the whole work. A clean removal leaves nothing stale behind, and
                # reporting it would be reporting an edit rather than an inconsistency.
                continue

            # A form of the same character that appeared where this one left. The registry is
            # what makes the two one person, so this is evidence rather than a guess — and it
            # is absent whenever the replacement is a name no reading has resolved yet.
            replaced_by = next(
                (
                    other
                    for other in forms
                    if other != form
                    and set(retired) <= (present_after[other] - present_before[other])
                ),
                None,
            )

            locations: list[Location] = []
            for path in elsewhere:
                document = after_by_path[path]
                for at in _occurrences(document.content, form):
                    locations.append(_location(document.id, path, document.content, at, form))

            findings.append(
                StaleName(
                    character_id=str(character.get("id")),
                    character_name=str(character.get("name")),
                    form=form,
                    retired_from=tuple(retired),
                    replaced_by=replaced_by,
                    locations=tuple(locations),
                )
            )

    return tuple(findings)


def _evidence_of(document: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for kind, key in (("character", "characters"), ("relation", "relations")):
        for entry in document.get(key) or []:
            for piece in entry.get("evidence") or []:
                yield kind, str(entry.get("id")), piece


def _lost_positions(
    store: Store, document: dict[str, Any], against: str, work: dict[str, Any]
) -> tuple[tuple[LostPosition, ...], tuple[str, ...]]:
    """Claims whose recorded position the revision being checked no longer has."""
    try:
        spec = spec_for_types(work.get("segment_types"))
    except StructureNotReproducible as error:
        return (), (f"positions could not be checked: {error}",)

    text = store.revision_text(against)
    segmentation = segment_text(text, spec)

    findings: list[LostPosition] = []
    for kind, identifier, piece in _evidence_of(document):
        locator = piece.get("locator") or {}
        path = list(locator.get("path") or [])
        if not path:
            continue
        try:
            find_passage(segmentation, path, document_id=locator.get("document_id"))
        except PassageNotFound:
            selector = piece.get("selector") or {}
            findings.append(
                LostPosition(
                    subject_kind=kind,
                    subject_id=identifier,
                    path=tuple(path),
                    quotation=str(selector.get("exact") or ""),
                    document_id=locator.get("document_id"),
                    words_survive=reanchor_selector(text, selector) is not None,
                )
            )

    return tuple(findings), ()


def _superseded(store: Store, documents: dict[str, Any]) -> tuple[SupersededDocument, ...]:
    """Documents another one revises, being read alongside the revision that replaced them."""
    by_path = _by_path(documents)
    if not by_path:
        return ()

    plans: dict[str, Any] = {}
    for root in store.structure_roots():
        plans.update(store.structure_map(root))

    findings: list[SupersededDocument] = []
    for path in sorted(by_path):
        plan = plans.get(path)
        if not isinstance(plan, dict):
            continue
        earlier = (plan.get("revision_of") or {}).get("value")
        if isinstance(earlier, str) and earlier in by_path:
            findings.append(
                SupersededDocument(
                    document_id=by_path[earlier].id, path=earlier, superseded_by=path
                )
            )

    return tuple(findings)


def report(
    store: Store,
    work_id: str,
    *,
    snapshot_id: str | None = None,
    against: str | None = None,
) -> Report:
    """Check a reading against the text as it now stands.

    ``snapshot_id`` defaults to the work's newest snapshot — the reading whose claims are
    being checked — and ``against`` to its newest text revision, which is the manuscript as it
    is. Where those name the same revision nothing has moved and the report says so.
    """
    work = store.get_work(work_id)
    if work is None:
        raise ContinuityError(f"unknown work {work_id!r}")

    snapshots = store.list_snapshots(work_id)
    if snapshot_id is None:
        if not snapshots:
            raise ContinuityError(f"{work['title']!r} has never been analysed")
        snapshot = snapshots[-1]
    else:
        found = store.get_snapshot(snapshot_id)
        if found is None or found.work_id != work_id:
            raise ContinuityError(f"no snapshot {snapshot_id!r} of this work")
        snapshot = found

    if against is None:
        revisions = store.list_text_revisions(work_id)
        if not revisions:
            raise ContinuityError(f"{work['title']!r} holds no text revision")
        against = revisions[-1].id
    elif store.get_text_revision(against) is None:
        raise ContinuityError(f"no text revision {against!r}")

    notes: list[str] = []
    stale: tuple[StaleName, ...] = ()
    lost: tuple[LostPosition, ...] = ()

    after = _documents_of(store, against)

    if against != snapshot.text_revision_id:
        before = _documents_of(store, snapshot.text_revision_id)
        stale = _stale_names(store, snapshot.document.get("characters") or [], before, after)
        lost, position_notes = _lost_positions(store, snapshot.document, against, work)
        notes.extend(position_notes)

    return Report(
        work_id=work_id,
        snapshot_id=snapshot.id,
        read_revision=snapshot.text_revision_id,
        against_revision=against,
        stale_names=stale,
        lost_positions=lost,
        # Checked whichever revision is being looked at: a superseded document read alongside
        # its replacement is wrong in the revision that holds them, not in the comparison.
        superseded=_superseded(store, after),
        notes=tuple(notes),
    )


def as_json(found: Report) -> dict[str, Any]:
    """The report as a document, for the API and for anything storing or printing it."""
    return {
        "work_id": found.work_id,
        "snapshot_id": found.snapshot_id,
        "read_revision": found.read_revision,
        "against_revision": found.against_revision,
        "unchanged": found.unchanged,
        "findings": len(found),
        "stale_names": [
            {
                "character_id": entry.character_id,
                "character_name": entry.character_name,
                "form": entry.form,
                "retired_from": list(entry.retired_from),
                "replaced_by": entry.replaced_by,
                "locations": [
                    {
                        "document_id": location.document_id,
                        "document_path": location.document_path,
                        "at": location.at,
                        "prefix": location.prefix,
                        "exact": location.exact,
                        "suffix": location.suffix,
                    }
                    for location in entry.locations
                ],
            }
            for entry in found.stale_names
        ],
        "lost_positions": [
            {
                "kind": entry.subject_kind,
                "id": entry.subject_id,
                "path": [dict(step) for step in entry.path],
                "document_id": entry.document_id,
                "quotation": entry.quotation,
                "words_survive": entry.words_survive,
            }
            for entry in found.lost_positions
        ],
        "superseded": [
            {
                "document_id": entry.document_id,
                "path": entry.path,
                "superseded_by": entry.superseded_by,
            }
            for entry in found.superseded
        ],
        "notes": list(found.notes),
    }

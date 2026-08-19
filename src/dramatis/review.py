"""Where human review of a claim stands.

A reading proposes. Everything a model returns is a proposal, however well evidenced, and
until somebody has looked at it that is all it is. This module is the record of somebody
having looked: per node and per edge, one of ``proposed``, ``accepted``, ``corrected``, or
``rejected`` (**5.1**).

Four things decide the shape, and each has an alternative that is worse.

**The snapshot is not touched.** Snapshots are immutable (Invariant 4) and a review happens
after one was written, so recording it in the stored document would mean rewriting an
artifact something may already cite. Decisions live in their own append-only table and are
read back *over* the document. What the snapshot says is what the analysis proposed; what
this says is what a person has since decided, and those are different facts that a reader is
entitled to see apart.

**A decision is about a claim, not about a document.** It is keyed by the work and the
subject's identifier, not by the snapshot, because identifiers are derived from content and
names rather than minted per run (`dramatis.ids`) — the same character is the same character
in the next reading of the same work. A decision scoped to a snapshot would expire every time
the analysis re-ran, and asking somebody to re-accept a cast they have already been through is
how a review tool stops being used. The snapshot the decision was taken in is recorded beside
it, because what was on the screen is part of what was decided.

**Nothing is overwritten.** Each decision is appended and the newest stands. A status column
updated in place would lose that somebody once accepted what has since been rejected, and
that history is the substance of phase 5's promise rather than decoration on it.

**A claim that was never made cannot be reviewed.** Recording a judgement about an identifier
no reading of the work proposed would put a decision in the store that nothing can ever show,
and the usual cause is a mistyped identifier rather than a considered act.

Nothing here calls a model or reaches a network (Invariant 6): review is a person's work, and
reading it back is arithmetic over what the store already holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dramatis.store import ReviewDecision, Store, StoredSnapshot, utc_now

PROPOSED = "proposed"
ACCEPTED = "accepted"
CORRECTED = "corrected"
REJECTED = "rejected"

STATUSES = (PROPOSED, ACCEPTED, CORRECTED, REJECTED)
"""The whole vocabulary, in the order a claim travels through it.

The same four the published schema enumerates and the store's ``CHECK`` constraint enforces.
Three copies of one list is two too many, so a test asserts they agree rather than trusting
that whoever edits one remembers the others.
"""

DEFAULT_STATUS = PROPOSED
"""Where every claim starts. A node nobody has looked at is proposed, not accepted."""

CHARACTER = "character"
RELATION = "relation"
SUBJECT_KINDS = (CHARACTER, RELATION)


class ReviewError(Exception):
    """A decision could not be recorded. The message says which rule refused it."""


@dataclass(frozen=True)
class SubjectStatus:
    """One node or edge, and where review of it stands."""

    kind: str
    id: str

    label: str
    """How to name this subject in a list.

    ASCII, because the CLI prints it to consoles that render typographic punctuation as
    replacement characters — the reason `IngestResult.summary` gives. The browser has the
    document in hand and builds its own titles, so nothing is lost by keeping this plain.
    """

    status: str = DEFAULT_STATUS
    note: str | None = None
    decided_at: str | None = None

    decided_in: str | None = None
    """The snapshot the decision was taken in, where one has been."""

    @property
    def reviewed(self) -> bool:
        """Whether a person has actually ruled on this, as against it merely being new.

        Not the same question as ``status != "proposed"``: somebody may look at a proposal,
        conclude that it is still only a proposal, and say so.
        """
        return self.decided_at is not None


@dataclass(frozen=True)
class ReviewOverlay:
    """Every subject of one snapshot, with the standing decision applied to each."""

    snapshot_id: str
    work_id: str
    subjects: tuple[SubjectStatus, ...] = ()

    def __len__(self) -> int:
        return len(self.subjects)

    @property
    def counts(self) -> dict[str, int]:
        """How many subjects sit at each status.

        Every status appears, including at zero: a missing key reads as "not applicable"
        when what is meant is "none yet".
        """
        tally = dict.fromkeys(STATUSES, 0)
        for subject in self.subjects:
            tally[subject.status] = tally.get(subject.status, 0) + 1
        return tally

    @property
    def reviewed(self) -> int:
        """How many subjects somebody has actually ruled on."""
        return sum(1 for subject in self.subjects if subject.reviewed)

    def entry_for(self, kind: str, identifier: str) -> SubjectStatus | None:
        return next((s for s in self.subjects if s.kind == kind and s.id == identifier), None)


def _names(document: dict[str, Any]) -> dict[str, str]:
    return {
        str(character.get("id")): str(character.get("name") or character.get("id"))
        for character in document.get("characters") or []
    }


def subjects(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Everything in a snapshot that can be reviewed, mapped to how it is named.

    Characters first, then relations, each in document order — the order the snapshot was
    written in, so two readings of one store list their subjects the same way.
    """
    names = _names(document)
    found: dict[tuple[str, str], str] = {}

    for character in document.get("characters") or []:
        identifier = str(character.get("id"))
        found[(CHARACTER, identifier)] = names.get(identifier, identifier)

    for relation in document.get("relations") or []:
        identifier = str(relation.get("id"))
        source = names.get(str(relation.get("source")), str(relation.get("source")))
        target = names.get(str(relation.get("target")), str(relation.get("target")))
        joiner = "->" if relation.get("directed") else "--"
        found[(RELATION, identifier)] = f"{source} {joiner} {target}"

    return found


def _declared_status(entry: Any) -> str:
    """What the document itself recorded, where it recorded anything.

    A snapshot may carry a ``review_status`` on a node or an edge — the schema has always
    allowed it — and that is the starting point a later decision supersedes. A document that
    says nothing falls back to ``proposed``, which is the schema's own default.
    """
    if isinstance(entry, dict):
        declared = entry.get("review_status")
        if isinstance(declared, str) and declared in STATUSES:
            return declared
    return DEFAULT_STATUS


def _declared(document: dict[str, Any]) -> dict[tuple[str, str], str]:
    declared: dict[tuple[str, str], str] = {}
    for character in document.get("characters") or []:
        declared[(CHARACTER, str(character.get("id")))] = _declared_status(character)
    for relation in document.get("relations") or []:
        declared[(RELATION, str(relation.get("id")))] = _declared_status(relation)
    return declared


def overlay(store: Store, snapshot: StoredSnapshot) -> ReviewOverlay:
    """Read a snapshot's subjects with every standing decision applied.

    The stored document is neither modified nor consulted for anything a decision
    supersedes. A subject nobody has ruled on carries whatever the document declared, which
    is ``proposed`` unless the analysis said otherwise.
    """
    standing = store.current_reviews(snapshot.work_id)
    declared = _declared(snapshot.document)

    entries = []
    for (kind, identifier), label in subjects(snapshot.document).items():
        decision = standing.get((kind, identifier))
        entries.append(
            SubjectStatus(
                kind=kind,
                id=identifier,
                label=label,
                status=decision.status if decision else declared[(kind, identifier)],
                note=decision.note if decision else None,
                decided_at=decision.decided_at if decision else None,
                decided_in=decision.snapshot_id if decision else None,
            )
        )

    return ReviewOverlay(snapshot_id=snapshot.id, work_id=snapshot.work_id, subjects=tuple(entries))


def _clean_note(note: str | None) -> str | None:
    """Whitespace is not a reason. A note of spaces is no note at all."""
    if note is None:
        return None
    return note.strip() or None


def record(
    store: Store,
    *,
    snapshot_id: str,
    subject_kind: str,
    subject_id: str,
    status: str,
    note: str | None = None,
    decided_at: str | None = None,
) -> ReviewDecision:
    """Record one person's decision about one node or edge.

    Four things are refused, each because the alternative writes something meaningless:

    * a status outside the vocabulary, which no reader could interpret;
    * a subject kind that is neither a node nor an edge;
    * a subject the named snapshot does not contain — see the module docstring;
    * ``corrected`` with nothing on the record saying what was corrected. A correction that
      does not state what it corrects is indistinguishable from a rejection somebody
      softened. A note satisfies this — and so, since **5.2**, does an actual correction to
      the subject, which says what changed in more detail than a sentence could. What is
      refused is the empty claim, not the missing sentence.

    Recording the decision that already stands is a no-op rather than a second identical row,
    so a client that re-sends on every render does not fill the log with restatements. A
    repeat that *changes the note* is a new decision: somebody has given their reason.
    """
    if status not in STATUSES:
        raise ReviewError(f"{status!r} is not a review status. Use one of: {', '.join(STATUSES)}.")
    if subject_kind not in SUBJECT_KINDS:
        raise ReviewError(
            f"{subject_kind!r} is not something that can be reviewed. "
            f"Use one of: {', '.join(SUBJECT_KINDS)}."
        )

    note = _clean_note(note)

    snapshot = store.get_snapshot(snapshot_id)
    if snapshot is None:
        raise ReviewError(f"no snapshot {snapshot_id!r}")

    if (subject_kind, subject_id) not in subjects(snapshot.document):
        raise ReviewError(
            f"snapshot {snapshot_id!r} proposes no {subject_kind} {subject_id!r}, so there "
            "is nothing there to review."
        )

    if (
        status == CORRECTED
        and note is None
        and not store.list_corrections(
            snapshot.work_id, subject_kind=subject_kind, subject_id=subject_id
        )
    ):
        raise ReviewError(
            "a correction must say what it corrects: pass a note alongside 'corrected', "
            "record the correction itself with `dramatis correct`, or use 'rejected' if the "
            "claim is simply wrong."
        )

    standing = store.list_reviews(
        snapshot.work_id, subject_kind=subject_kind, subject_id=subject_id
    )
    if standing and standing[-1].status == status and standing[-1].note == note:
        return standing[-1]

    return store.append_review(
        ReviewDecision(
            work_id=snapshot.work_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            status=status,
            snapshot_id=snapshot.id,
            decided_at=decided_at or utc_now(),
            note=note,
        )
    )


def history(store: Store, work_id: str, subject_kind: str, subject_id: str) -> list[ReviewDecision]:
    """Every decision ever taken about one subject, oldest first.

    The point of an append-only log is that this question has an answer. A reviewer asking
    why an edge is rejected when they remember accepting it is asking for exactly this.
    """
    return store.list_reviews(work_id, subject_kind=subject_kind, subject_id=subject_id)


def subject_as_json(subject: SubjectStatus) -> dict[str, Any]:
    """One subject's state.

    Fields nobody has decided are present and null rather than absent: a client merging this
    into a map it already holds needs to be able to clear a stale entry, and an absent key
    cannot say "there is no note".
    """
    return {
        "kind": subject.kind,
        "id": subject.id,
        "label": subject.label,
        "status": subject.status,
        "note": subject.note,
        "decided_at": subject.decided_at,
        "decided_in": subject.decided_in,
        "reviewed": subject.reviewed,
    }


def as_json(state: ReviewOverlay) -> dict[str, Any]:
    """The overlay as a document, for the API and for anything printing it."""
    return {
        "snapshot_id": state.snapshot_id,
        "work_id": state.work_id,
        "counts": state.counts,
        "reviewed": state.reviewed,
        "subjects": [subject_as_json(subject) for subject in state.subjects],
    }

"""Correcting what a reading got wrong, so the correction outlives the reading.

**5.1** let a person say *this is wrong*. This lets them say *this is what it should be*, and
makes the answer stick: a correction is applied to every snapshot built afterwards, so it
survives re-analysis rather than having to be made again each time a model is run.

Four rules shape it, and each has an alternative that loses somebody's work.

**A correction is applied when a snapshot is built, never to one already written.** Snapshots
are immutable (Invariant 4). A correction made against snapshot *n* is recorded beside it —
the same place **5.1** puts a review — and is written into *n+1* as the graph is rendered. The
snapshot on screen goes on saying what the analysis said, because that is what it was; the
correction says what a person decided since, and the two are different facts.

**A corrected node or edge is `human`.** Invariant 5 defines `human` as *entered or corrected
in the app*, so this is not a choice this module makes. The consequence is real and intended:
a corrected relation leaves **4.4**'s declared-against-enacted comparison, which counts it
separately as entered by hand rather than pretending a person's edit is evidence about the
corpus.

**A later reading is never silently overruled, and never silently wins.** Each correction
records what the reading said at the moment it was made. When a later analysis proposes
something different, the correction still stands — a person outranks a run — but the run's
competing claim is written down as a conflict and reported. The alternative in one direction
throws away the person's work and in the other hides that the model has changed its mind.

**Only what a person can actually judge may be corrected.** A name, a kind, an alias list, a
relation's types, its tone, its direction, a note. Not a weight, which is a count on a
declared basis and not an opinion; not evidence, which is verified against the text or
rejected (Invariant 3); not identity, which is **5.3**'s merge and split. Every refusal names
its reason, because "unknown field" teaches nobody anything.

Nothing here calls a model or reaches a network (Invariant 6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dramatis.review import CHARACTER, RELATION, SUBJECT_KINDS, subjects
from dramatis.store import Correction, CorrectionConflict, Store, utc_now

CHARACTER_KINDS = ("person", "collective", "entity", "unknown")
"""The schema's own enumeration for a character's kind, repeated so a correction is refused
here with an explanation rather than by the validator with a JSON pointer."""


class CorrectionError(Exception):
    """A correction could not be recorded. The message says which rule refused it."""


@dataclass(frozen=True)
class Field:
    """One correctable field: what shape its value takes, and how to say so."""

    name: str
    shape: str
    """A phrase for the error message — "a name", "a list of names", "-1 to +1"."""


CHARACTER_FIELDS: tuple[Field, ...] = (
    Field("name", "a non-empty name"),
    Field("kind", f"one of {', '.join(CHARACTER_KINDS)}"),
    Field("aliases", "a list of surface forms"),
    Field("notes", "free text, empty to remove the note"),
)

RELATION_FIELDS: tuple[Field, ...] = (
    Field("types", "a list of relation types"),
    Field("valence", "a number from -1 (hostile) to +1 (affectionate)"),
    Field("directed", "true or false"),
    Field("notes", "free text, empty to remove the note"),
)

CORRECTABLE: dict[str, tuple[Field, ...]] = {
    CHARACTER: CHARACTER_FIELDS,
    RELATION: RELATION_FIELDS,
}

REFUSED: dict[str, str] = {
    "id": "identity is not a field: merging or splitting a character is 5.3",
    "source": "identity is not a field: merging or splitting a character is 5.3",
    "target": "identity is not a field: merging or splitting a character is 5.3",
    "weight": (
        "a weight is a count on a declared basis, not an opinion. Correcting the relation's "
        "types or tone says what you mean without claiming a tally the evidence does not show"
    ),
    "weight_basis": "a weight basis names what was counted, and nothing counted it differently",
    "evidence": (
        "evidence is verified against the source text or it is not stored (Invariant 3), so "
        "it cannot be typed in"
    ),
    "provenance": "provenance follows from correcting: a corrected node or edge is 'human'",
    "review_status": "review status is set by `dramatis review` (5.1), not by correcting",
    "confidence": "confidence is a reading's estimate of itself; a person's is not on that scale",
    "salience": "salience is computed from the reading, so a typed-in figure would not mean it",
}
"""Fields somebody may plausibly try to correct, and why each is refused.

Listed rather than left to fall through to "unknown field", because every one of these is a
reasonable thing to want and the reason it is declined is the useful part of the answer.
"""


def fields_for(subject_kind: str) -> tuple[str, ...]:
    return tuple(entry.name for entry in CORRECTABLE.get(subject_kind, ()))


def _describe(subject_kind: str) -> str:
    return ", ".join(f"{entry.name} ({entry.shape})" for entry in CORRECTABLE.get(subject_kind, ()))


def _clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    return note.strip() or None


def normalise(subject_kind: str, name: str, value: Any) -> Any:
    """Check a value against its field's shape, and put it in the form the schema wants.

    Raises `CorrectionError` rather than returning a flag: a correction that cannot be stored
    as the type it claims is not a correction, and letting it through would move the failure
    to schema validation, where the message names a JSON pointer instead of the mistake.
    """
    if name in ("name",):
        if not isinstance(value, str) or not value.strip():
            raise CorrectionError(f"{name!r} must be {_shape(subject_kind, name)}")
        return value.strip()

    if name == "kind":
        if value not in CHARACTER_KINDS:
            raise CorrectionError(
                f"{value!r} is not a character kind. Use one of: {', '.join(CHARACTER_KINDS)}."
            )
        return value

    if name in ("aliases", "types"):
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise CorrectionError(f"{name!r} must be {_shape(subject_kind, name)}")
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return cleaned

    if name == "valence":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CorrectionError(f"{name!r} must be {_shape(subject_kind, name)}")
        if not -1 <= float(value) <= 1:
            raise CorrectionError(f"a valence of {value} is outside -1 to +1")
        return float(value)

    if name == "directed":
        if not isinstance(value, bool):
            raise CorrectionError(f"{name!r} must be {_shape(subject_kind, name)}")
        return value

    if name == "notes":
        if not isinstance(value, str):
            raise CorrectionError(f"{name!r} must be {_shape(subject_kind, name)}")
        return value.strip()

    raise CorrectionError(f"{name!r} is not a correctable field")  # pragma: no cover - guarded


def _shape(subject_kind: str, name: str) -> str:
    for entry in CORRECTABLE.get(subject_kind, ()):
        if entry.name == name:
            return entry.shape
    return "a value"  # pragma: no cover - guarded by check_field


def check_field(subject_kind: str, name: str) -> None:
    """Refuse a field that may not be corrected, saying why rather than that it is unknown."""
    if subject_kind not in SUBJECT_KINDS:
        raise CorrectionError(
            f"{subject_kind!r} is not something that can be corrected. "
            f"Use one of: {', '.join(SUBJECT_KINDS)}."
        )
    if name in fields_for(subject_kind):
        return
    if name in REFUSED:
        raise CorrectionError(f"{name!r} cannot be corrected: {REFUSED[name]}")
    other = RELATION if subject_kind == CHARACTER else CHARACTER
    if name in fields_for(other):
        raise CorrectionError(
            f"{name!r} is a field of a {other}, not of a {subject_kind}. "
            f"A {subject_kind} takes: {_describe(subject_kind)}."
        )
    raise CorrectionError(
        f"{name!r} is not a field of a {subject_kind}. It takes: {_describe(subject_kind)}."
    )


def _entries(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Every reviewable entry of a document, by (kind, id), as the live dict to be edited."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in document.get("characters") or []:
        found[(CHARACTER, str(entry.get("id")))] = entry
    for entry in document.get("relations") or []:
        found[(RELATION, str(entry.get("id")))] = entry
    return found


def reading_says(entry: dict[str, Any], name: str) -> Any:
    """What a document holds for a field, with absence reported as ``None``.

    Absence is a real answer and a common one: a run that recorded no relation types did not
    record an empty list, it said nothing. Comparing an absent field against ``[]`` would call
    every such field a disagreement.
    """
    return entry.get(name)


@dataclass(frozen=True)
class Conflict:
    """A reading proposing something other than what a correction replaced."""

    subject_kind: str
    subject_id: str
    field: str
    proposed: Any
    held: Any

    def __str__(self) -> str:
        return (
            f"this reading gives {self.subject_kind} {self.subject_id} "
            f"{self.field}={json.dumps(self.proposed, ensure_ascii=False)}, but a correction "
            f"holds {json.dumps(self.held, ensure_ascii=False)}. The correction stands."
        )


@dataclass(frozen=True)
class Application:
    """A document with every standing correction written into it, and what that turned up."""

    document: dict[str, Any]
    applied: tuple[Correction, ...] = ()
    conflicts: tuple[Conflict, ...] = ()

    missing: tuple[Correction, ...] = field(default_factory=tuple)
    """Corrections whose subject this reading does not contain.

    Reported rather than dropped, and never resurrected: putting back a character the reading
    did not find would invent a node with no evidence behind it. What is owed here is telling
    somebody their work no longer has anything to attach to.
    """

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(str(conflict) for conflict in self.conflicts) + tuple(
            f"a correction to {correction.subject_kind} {correction.subject_id} "
            f"({correction.field}) could not be applied: this reading does not contain it"
            for correction in self.missing
        )


def apply(store: Store, document: dict[str, Any]) -> Application:
    """Write every standing correction into a freshly built document.

    Pure with respect to the store: it reads corrections and returns a new document, leaving
    the recording of conflicts to the caller that knows which snapshot they belong to.
    """
    work_id = str(document.get("snapshot", {}).get("work_id", ""))
    standing = store.current_corrections(work_id)
    if not standing:
        return Application(document=document)

    corrected = json.loads(json.dumps(document))
    entries = _entries(corrected)

    applied: list[Correction] = []
    conflicts: list[Conflict] = []
    missing: list[Correction] = []

    for (subject_kind, subject_id, name), correction in standing.items():
        entry = entries.get((subject_kind, subject_id))
        if entry is None:
            missing.append(correction)
            continue

        proposed = reading_says(entry, name)
        if proposed != correction.was:
            conflicts.append(
                Conflict(
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    field=name,
                    proposed=proposed,
                    held=correction.value,
                )
            )

        # An empty value removes the key rather than writing a blank one. The schema treats an
        # absent field as "the run never said", and a person clearing a note means exactly
        # that rather than "the note is the empty string".
        if correction.value in ("", []):
            entry.pop(name, None)
        else:
            entry[name] = correction.value

        # Invariant 5: corrected in the app is `human`. The review status follows too, so a
        # graph read without the review overlay still shows that somebody has been here.
        entry["provenance"] = "human"
        entry["review_status"] = "corrected"
        applied.append(correction)

    return Application(
        document=corrected,
        applied=tuple(applied),
        conflicts=tuple(conflicts),
        missing=tuple(missing),
    )


def record_conflicts(
    store: Store, *, work_id: str, snapshot_id: str, conflicts: Sequence[Conflict]
) -> int:
    """Write down what a reading proposed where a correction overruled it."""
    noticed_at = utc_now()
    return store.append_correction_conflicts(
        [
            CorrectionConflict(
                work_id=work_id,
                subject_kind=conflict.subject_kind,
                subject_id=conflict.subject_id,
                field=conflict.field,
                proposed=conflict.proposed,
                held=conflict.held,
                snapshot_id=snapshot_id,
                noticed_at=noticed_at,
            )
            for conflict in conflicts
        ]
    )


def record(
    store: Store,
    *,
    snapshot_id: str,
    subject_kind: str,
    subject_id: str,
    field: str,
    value: Any,
    note: str | None = None,
    corrected_at: str | None = None,
) -> Correction:
    """Record a person's replacement for one field of one node or edge.

    The snapshot named is the reading the correction was made against: what it says for that
    field becomes the correction's ``was``, which is what a later reading is measured against.

    A correction also sets the subject's review status to `corrected` (**5.1**), because the
    two are one act. That status previously demanded a note explaining what was corrected;
    from here a recorded correction is that explanation, and the note is optional.

    Recording the correction that already stands is a no-op rather than a second identical
    row. Recording the same value with a different note is a new correction: somebody has
    given their reason.
    """
    from dramatis.review import CORRECTED
    from dramatis.review import record as record_review

    check_field(subject_kind, field)
    value = normalise(subject_kind, field, value)
    note = _clean_note(note)

    snapshot = store.get_snapshot(snapshot_id)
    if snapshot is None:
        raise CorrectionError(f"no snapshot {snapshot_id!r}")

    if (subject_kind, subject_id) not in subjects(snapshot.document):
        raise CorrectionError(
            f"snapshot {snapshot_id!r} proposes no {subject_kind} {subject_id!r}, so there is "
            "nothing there to correct."
        )

    entry = _entries(snapshot.document)[(subject_kind, subject_id)]
    was = reading_says(entry, field)

    standing = store.list_corrections(
        snapshot.work_id, subject_kind=subject_kind, subject_id=subject_id, field=field
    )
    if standing and standing[-1].value == value and standing[-1].note == note:
        return standing[-1]

    correction = store.append_correction(
        Correction(
            work_id=snapshot.work_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            field=field,
            value=value,
            was=was,
            snapshot_id=snapshot.id,
            corrected_at=corrected_at or utc_now(),
            note=note,
        )
    )

    # After the correction is on the record, so 5.1's rule about an unexplained `corrected`
    # is satisfied by the correction itself rather than by a note repeating it.
    record_review(
        store,
        snapshot_id=snapshot.id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        status=CORRECTED,
        note=note,
    )
    return correction


def history(store: Store, work_id: str, subject_kind: str, subject_id: str) -> list[Correction]:
    """Every correction ever made to one subject, oldest first."""
    return store.list_corrections(work_id, subject_kind=subject_kind, subject_id=subject_id)


def correction_as_json(correction: Correction) -> dict[str, Any]:
    return {
        "kind": correction.subject_kind,
        "id": correction.subject_id,
        "field": correction.field,
        "value": correction.value,
        "was": correction.was,
        "note": correction.note,
        "corrected_at": correction.corrected_at,
        "corrected_in": correction.snapshot_id,
    }


def conflict_as_json(conflict: CorrectionConflict) -> dict[str, Any]:
    return {
        "kind": conflict.subject_kind,
        "id": conflict.subject_id,
        "field": conflict.field,
        "proposed": conflict.proposed,
        "held": conflict.held,
        "noticed_at": conflict.noticed_at,
        "noticed_in": conflict.snapshot_id,
    }


def as_json(store: Store, snapshot_id: str, work_id: str) -> dict[str, Any]:
    """Standing corrections for a work, and the disagreements this reading raised with them.

    Both together, because they are one question: what has a person changed, and did the
    reading on screen argue with any of it.
    """
    standing = store.current_corrections(work_id)
    return {
        "snapshot_id": snapshot_id,
        "work_id": work_id,
        "corrections": [correction_as_json(correction) for correction in standing.values()],
        "conflicts": [
            conflict_as_json(conflict)
            for conflict in store.list_correction_conflicts(work_id, snapshot_id=snapshot_id)
        ],
        "correctable": {kind: list(fields_for(kind)) for kind in SUBJECT_KINDS},
        # Served rather than left for a client to hardcode, for the same reason `correctable`
        # is: a fourth copy of a vocabulary is a fourth place for it to drift.
        "character_kinds": list(CHARACTER_KINDS),
    }

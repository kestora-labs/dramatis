"""Deciding who is who: merging two characters into one, and splitting one into two.

Resolution can create a character and attach a form to an existing one. It cannot merge two
the registry already knows, and that is structural rather than guarded — a form the registry
claims is resolved directly and never reaches the grouping stage. The reason is written down
in `resolution`: *merging is destructive and cannot be reviewed after the fact, so it stays a
human act*. This is that act.

Four things shape it.

**Both operations are one shape: surface forms moving between characters.** A merge moves all
of a character's forms to another and retires it. A split moves some of a character's forms to
a new one and leaves it standing. Holding them as one shape is not tidiness — it is what lets
a split undo a merge, which is the only undo either has.

**The decision takes effect through the registry, not through a rewrite.** Nothing edits a
stored snapshot, and nothing here has to teach the pipeline anything: the next reading resolves
the moved forms to the character that now claims them, and aggregation groups edges by
character, so the graph comes out merged or split on its own. That the registry is the whole
mechanism is why the bullet says *recorded in the registry*.

**A retired character is retired, not deleted.** Snapshots written before the merge name its
identifier, and a reader following one back is owed an answer. It keeps its row, loses its
forms — so nothing can resolve to it again — and points at the character that absorbed it.

**Human work follows the character.** Reviews (**5.1**) and corrections (**5.2**) are recorded
against an identifier, and a merge would strand everything recorded against the absorbed one.
They are read through `merged_into` instead, so a rejection or a correction made before a merge
goes on applying to the character that survived it. Losing that would make this bullet undo the
two before it.

Nothing here calls a model or reaches a network (Invariant 6).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from dramatis import ids
from dramatis.store import (
    AmbiguousAliasError,
    Correspondence,
    RegisteredCharacter,
    RegistryDecision,
    Store,
    form_key,
    utc_now,
)

MERGE = "merge"
SPLIT = "split"


class IdentityError(Exception):
    """A merge or a split was refused. The message says which rule refused it."""


@dataclass(frozen=True)
class Merge:
    """What a merge did, for a caller that has to report it."""

    decision: RegistryDecision
    survivor: RegisteredCharacter
    absorbed: RegisteredCharacter
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Split:
    """What a split did."""

    decision: RegistryDecision
    source: RegisteredCharacter
    created: RegisteredCharacter
    warnings: tuple[str, ...] = ()


def _require(store: Store, collection_id: str, identifier: str) -> RegisteredCharacter:
    character = store.get_character(identifier)
    if character is None or character.collection_id != collection_id:
        raise IdentityError(f"{identifier!r} is not a character in this collection")
    if character.retired:
        raise IdentityError(
            f"{identifier!r} was already merged into {character.merged_into!r}. "
            "Merge the character that absorbed it, or split it back out first."
        )
    return character


def _alias_corrections(store: Store, identifier: str) -> tuple[str, ...]:
    """Works where a standing correction governs this character's alias list (**5.2**).

    A correction to `aliases` replaces the whole list when a snapshot is built, so it would
    also drop the forms a merge has just handed over — and with them the record `diff` reads
    to recognise the merge at all. Reported rather than resolved: a merge and a correction are
    both a person's decision, and this is not the place to pick between them.
    """
    found: list[str] = []
    for work in store.list_works():
        standing = store.current_corrections(str(work["id"]))
        if ("character", identifier, "aliases") in standing:
            found.append(str(work["title"]))
    return tuple(found)


def merge(
    store: Store,
    collection_id: str,
    *,
    into: str,
    absorb: str,
    note: str | None = None,
    decided_at: str | None = None,
) -> Merge:
    """Declare that two registered characters are one person.

    ``absorb``'s surface forms — its own name included — become ``into``'s, and ``absorb`` is
    retired. The surviving character keeps its name, so the graph goes on calling the person
    what it called them; what changes is how many names reach them.

    The survivor's kind is kept unless it is `unknown` and the absorbed one's is not, which is
    the rule resolution already applies when a group joins a registered character. Its notes
    are kept, and the absorbed one's are taken only where the survivor had none — nothing a
    person wrote is discarded, and nothing they wrote is overwritten.
    """
    if into == absorb:
        raise IdentityError("a character cannot be merged into itself")

    survivor = _require(store, collection_id, into)
    absorbed = _require(store, collection_id, absorb)

    moved = tuple(absorbed.surface_forms)
    aliases = tuple(
        dict.fromkeys(
            form
            for form in (*survivor.aliases, *moved)
            if form_key(form) != form_key(survivor.name)
        )
    )

    merged = replace(
        survivor,
        kind=survivor.kind if survivor.kind != "unknown" else absorbed.kind,
        notes=survivor.notes or absorbed.notes,
        # 5.1's vocabulary: somebody has been here and changed what the reading proposed. The
        # provenance is deliberately left alone — see the module note in DECISIONS. The
        # character is still enacted by the narrative; what a person settled is who it is.
        review_status="corrected",
        aliases=aliases,
    )
    emptied = replace(absorbed, aliases=(), merged_into=survivor.id)

    try:
        store.rewrite_characters([merged, emptied], retire={absorbed.id: survivor.id})
    except AmbiguousAliasError as error:  # pragma: no cover - unreachable via this path
        raise IdentityError(str(error)) from error

    decision = store.append_registry_decision(
        RegistryDecision(
            collection_id=collection_id,
            action=MERGE,
            source_id=absorbed.id,
            target_id=survivor.id,
            forms=moved,
            decided_at=decided_at or utc_now(),
            note=note,
        )
    )

    warnings = tuple(
        f"a standing correction governs this character's aliases in {title!r}, so the "
        "absorbed names will not appear there until that correction is updated"
        for title in _alias_corrections(store, survivor.id)
    )

    survived = store.get_character(survivor.id) or merged
    gone = store.get_character(absorbed.id) or emptied
    return Merge(decision=decision, survivor=survived, absorbed=gone, warnings=warnings)


def split(
    store: Store,
    collection_id: str,
    *,
    character: str,
    forms: Sequence[str],
    name: str | None = None,
    note: str | None = None,
    decided_at: str | None = None,
) -> Split:
    """Declare that one registered character is two people.

    The named forms move to a new character; everything else stays. The new character is called
    ``name``, or the first form moved.

    A split that would move every form is refused. That is a rename, not a split — there would
    be no second person, only the same one under another identifier, and the registry would
    have lost the identity that made two snapshots comparable.
    """
    source = _require(store, collection_id, character)

    wanted = [str(form).strip() for form in forms if str(form).strip()]
    if not wanted:
        raise IdentityError("a split needs at least one surface form to move")

    held = {form_key(form): form for form in source.surface_forms}
    unknown = [form for form in wanted if form_key(form) not in held]
    if unknown:
        raise IdentityError(
            f"{source.id!r} does not answer to {', '.join(repr(form) for form in unknown)}. "
            f"It answers to: {', '.join(source.surface_forms)}."
        )

    moving_keys = {form_key(form) for form in wanted}
    moved = tuple(held[key] for key in dict.fromkeys(form_key(form) for form in wanted))
    remaining = [form for form in source.surface_forms if form_key(form) not in moving_keys]
    if not remaining:
        raise IdentityError(
            "a split must leave at least one surface form behind. Moving every form is a "
            "rename, not a split, and would leave nobody where the character was."
        )

    chosen = (name or moved[0]).strip()
    if not chosen:
        raise IdentityError("the new character needs a name")
    if form_key(chosen) not in moving_keys and store.find_character_by_form(collection_id, chosen):
        raise IdentityError(
            f"{chosen!r} already denotes another character in this collection. Name the new "
            "character something the registry does not already claim."
        )

    kept = replace(
        source,
        name=remaining[0],
        aliases=tuple(remaining[1:]),
        review_status="corrected",
    )

    identifier = ids.character_id(chosen)
    if identifier == source.id or store.get_character(identifier) is not None:
        identifier = ids.character_id(chosen, disambiguator=form_key(moved[0])[:8])

    created = RegisteredCharacter(
        id=identifier,
        collection_id=collection_id,
        name=chosen,
        kind=source.kind,
        # Invariant 5: a character a person separated out by hand is one they entered.
        # Unlike a merge, this puts a node in the graph that no reading proposed.
        provenance="human",
        review_status="corrected",
        aliases=tuple(form for form in moved if form_key(form) != form_key(chosen)),
    )

    try:
        store.rewrite_characters([kept, created])
    except AmbiguousAliasError as error:
        raise IdentityError(str(error)) from error

    decision = store.append_registry_decision(
        RegistryDecision(
            collection_id=collection_id,
            action=SPLIT,
            source_id=source.id,
            target_id=created.id,
            forms=moved,
            decided_at=decided_at or utc_now(),
            note=note,
        )
    )

    warnings = tuple(
        f"a standing correction governs this character's aliases in {title!r}, so the moved "
        "names will reappear there until that correction is updated"
        for title in _alias_corrections(store, source.id)
    )

    return Split(
        decision=decision,
        source=store.get_character(source.id) or kept,
        created=store.get_character(created.id) or created,
        warnings=warnings,
    )


# -- correspondence across editions (6.4) ---------------------------------------------
#
# The third operation, and the one that is defined by what it refuses to do. A merge makes
# two characters one; this leaves both standing and says they are the same figure in two
# editions. Fixture D is explicit that the difference matters:
#
#   "Resolve within an edition, map across editions. Merging the two into one node that
#   belongs to neither loses the ability to answer 'who is in the 1889 text?', which is the
#   question this shape exists to serve."
#
# Merging Perdita into Hesper would leave the 1903 graph showing a node captioned Hesper — a
# name that does not occur anywhere in the 1903 text. That is not a tidier registry, it is a
# false claim about a published edition.

CORRESPOND = "correspond"


@dataclass(frozen=True)
class Correspond:
    """What a correspondence did, for a caller that has to report it."""

    left: RegisteredCharacter
    right: RegisteredCharacter
    note: str | None = None

    @property
    def summary(self) -> str:
        # ASCII only, for the reason IngestResult.summary gives.
        return f"{self.left.name} and {self.right.name} are one figure across editions"


def _editions_of(store: Store, collection_id: str, identifier: str) -> set[str]:
    """Which works a character is currently found in.

    Read from the registry rather than from the character row, because appearing in an
    edition is a fact about what a reading found, not about what somebody typed.
    """
    from dramatis.registry import build_registry

    entry = next(
        (item for item in build_registry(store, collection_id).entries if item.id == identifier),
        None,
    )
    return set(entry.work_ids) if entry else set()


def correspond(
    store: Store,
    collection_id: str,
    first: str,
    second: str,
    *,
    note: str | None = None,
) -> Correspond:
    """Declare that two characters are one figure across two editions (**6.4**).

    Neither character is changed. No surface form moves, nothing is retired, and every
    snapshot already written goes on saying exactly what it said — which is the whole point:
    the 1889 graph keeps Hesper and the 1903 graph keeps Perdita, and this is the record that
    lets a reader comparing them see one person.

    Nothing here calls a model or reaches a network (Invariant 6).
    """
    left = _require(store, collection_id, first)
    right = _require(store, collection_id, second)

    if left.id == right.id:
        raise IdentityError(f"{left.id} is already itself; a correspondence needs two characters")

    shared = _editions_of(store, collection_id, left.id) & _editions_of(
        store, collection_id, right.id
    )
    if shared:
        # The distinction the whole operation exists for. Two characters a reading found in
        # one edition are two characters in that edition, and deciding they are one person is
        # a merge — which moves the surface forms and retires one of them.
        where = ", ".join(sorted(shared))
        raise IdentityError(
            f"{left.name} and {right.name} both appear in {where}, so this is not a "
            "cross-edition correspondence. Two characters in one text who are the same "
            "person is a merge: `dramatis merge` moves the surface forms and retires one."
        )

    recorded = store.upsert_correspondence(
        Correspondence(
            collection_id=collection_id,
            left_id=left.id,
            right_id=right.id,
            note=note,
            decided_at=utc_now(),
        )
    )
    return Correspond(left=left, right=right, note=recorded.note)


def withdraw(store: Store, collection_id: str, first: str, second: str) -> bool:
    """Remove a correspondence. Returns whether there was one to remove.

    A plain delete, not a superseding record, and deliberately unlike **5.1**'s append-only
    reviews: a correspondence is a statement that two identifiers denote one figure, and a
    withdrawn one leaves nothing behind that a later reading could act on. There is no state
    for the history to explain.
    """
    return store.delete_correspondence(collection_id, first, second)


def correspondents(store: Store, collection_id: str) -> dict[str, str]:
    """Every corresponded identifier mapped to its counterpart, both ways round.

    What a diff needs: comparing the 1889 reading against the 1903 one has to be able to
    look up either side. A character in more than one correspondence keeps only the first,
    which is a limitation rather than a decision — three editions is a real case and it is
    not this bullet's.
    """
    found: dict[str, str] = {}
    for entry in store.list_correspondences(collection_id):
        found.setdefault(entry.left_id, entry.right_id)
        found.setdefault(entry.right_id, entry.left_id)
    return found


def describe_correspondences(store: Store, collection_id: str) -> list[str]:
    """One ASCII line per declared pair, for a console."""
    names = {c.id: c.name for c in store.list_characters(collection_id, include_retired=True)}
    lines = []
    for entry in store.list_correspondences(collection_id):
        left = names.get(entry.left_id, entry.left_id)
        right = names.get(entry.right_id, entry.right_id)
        line = f"{left} = {right} across editions"
        if entry.note:
            line += f" ({entry.note})"
        lines.append(line)
    return lines


def redirect(store: Store, collection_id: str) -> dict[str, str]:
    """Where each retired character's identifier now points.

    What **5.1** and **5.2** read so their records survive a merge. Empty for a collection
    nobody has merged in, which is the common case and costs one query.
    """
    return store.merged_into(collection_id)


def follow(redirects: dict[str, str], identifier: str) -> str:
    """One identifier, seen through any merge. Returns it unchanged when nothing moved."""
    return redirects.get(identifier, identifier)


def decisions_as_json(store: Store, collection_id: str) -> list[dict[str, object]]:
    """Every merge and split in a collection, for the API and for anything printing them."""
    return [
        {
            "action": decision.action,
            "source_id": decision.source_id,
            "target_id": decision.target_id,
            "forms": list(decision.forms),
            "note": decision.note,
            "decided_at": decision.decided_at,
        }
        for decision in store.list_registry_decisions(collection_id)
    ]


def retired_characters(store: Store, collection_id: str) -> list[RegisteredCharacter]:
    """Characters a person merged away, so an identifier in an old snapshot can be traced."""
    return [
        character
        for character in store.list_characters(collection_id, include_retired=True)
        if character.retired
    ]


def describe(entries: Iterable[RegistryDecision]) -> list[str]:
    """One line per decision, for a console. ASCII, for the reason `IngestResult` gives."""
    lines = []
    for decision in entries:
        arrow = "->" if decision.action == MERGE else "=>"
        lines.append(
            f"{decision.decided_at}  {decision.action:<5} "
            f"{decision.source_id} {arrow} {decision.target_id}  "
            f"({', '.join(decision.forms)})"
        )
    return lines

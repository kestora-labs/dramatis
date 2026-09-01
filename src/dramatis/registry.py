"""Where each character in a collection actually appears.

A collection is a set of works sharing one character registry, and that sharing already
works: `ingest` puts a second work into the collection the project already holds, and
`resolution` matches surface forms against every character the collection knows, so *Chief
Mbeki* in the second novel resolves to the *Ada Mbeki* the first one registered. What was
missing is the ability to ask the obvious next question — **where does this character
appear?** — which is the whole point of a registry that spans works.

**Appearances are derived from snapshots, never stored.** A `character_works` table would be
a second source of truth needing to be kept in step with the snapshots it summarises, and the
symptom of it falling behind is a character reported in a work they were cut from. Snapshots
are immutable (Invariant 4), so deriving from them is stable: the same store always yields
the same answer, and no write path can forget to update it.

**Only the newest snapshot of each work is consulted, and the answer says so.** A character
in the first draft of a novel and cut from the second does not appear in that novel, and a
view that read every snapshot ever taken would go on asserting they do. The snapshot each
claim rests on is named, so the claim can be checked — the same reason every proposal in
`structure` carries its basis.

**Nothing here reads a model or a network** (Invariant 6). It is arithmetic over what the
store already holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dramatis.store import RegisteredCharacter, RegistryDecision, Store


class RegistryError(Exception):
    """The registry could not be read. The message names the collection."""


@dataclass(frozen=True)
class Appearance:
    """One work a character was found in, and the reading that found them."""

    work_id: str
    work_title: str
    snapshot_id: str
    """The snapshot this rests on, so the claim can be checked rather than taken."""

    relations: int
    """How many relations the character has in that work — a cheap sense of how large a
    part they play there, on that work's own scale and never compared across works."""

    edition: str | None = None
    """Which edition of the work this is, where the work names one (**6.4**).

    Two editions are two works sharing this registry, so a character in both produces two
    appearances whose titles are identical. The edition is what tells them apart.
    """

    @property
    def named(self) -> str:
        """The work as it must be written wherever a number is written beside it.

        `_run_status` gives the reason and it applies with more force here, because these
        titles carry a relation count: two rows differing only in an identifier suffix is how
        somebody reads one edition's numbers as the other's. A work with no edition is named
        exactly as it always was.
        """
        return f"{self.work_title} [{self.edition}]" if self.edition else self.work_title


@dataclass(frozen=True)
class RegistryEntry:
    """A character, and everywhere in the collection they turn up."""

    character: RegisteredCharacter
    appearances: tuple[Appearance, ...] = ()

    @property
    def id(self) -> str:
        return self.character.id

    @property
    def name(self) -> str:
        return self.character.name

    @property
    def spans(self) -> bool:
        """Whether this character crosses more than one work — 4.5's question."""
        return len(self.appearances) > 1

    @property
    def work_ids(self) -> tuple[str, ...]:
        return tuple(appearance.work_id for appearance in self.appearances)


@dataclass(frozen=True)
class Registry:
    collection_id: str
    collection_name: str
    entries: tuple[RegistryEntry, ...] = ()
    works: tuple[tuple[str, str, str | None], ...] = ()
    """(id, title, edition) for every work in the collection, including any with no snapshot
    yet. The edition is None for a work that does not name one, which is most of them."""

    decisions: tuple[RegistryDecision, ...] = field(default_factory=tuple)
    """Every merge and split somebody has made here (**5.3**).

    Carried with the cast rather than fetched separately, because the cast is the outcome of
    these decisions and reading one without the other invites mistaking a curated identity for
    one a model proposed.
    """

    retired: tuple[RegisteredCharacter, ...] = field(default_factory=tuple)
    """Characters merged away, kept so an identifier in an older snapshot can be traced."""

    unanalysed: tuple[tuple[str, str | None], ...] = field(default_factory=tuple)
    """(title, edition) for every work holding no snapshot.

    Named rather than left out. A character absent from the registry because a work has never
    been analysed looks exactly like a character who is not in it, and only this tells the
    two apart. The edition is carried for the reason `Appearance.named` gives: two editions of
    one work reported by title alone are one sentence printed twice.
    """

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def spanning(self) -> tuple[RegistryEntry, ...]:
        """Every character appearing in more than one work."""
        return tuple(entry for entry in self.entries if entry.spans)

    def entry_for(self, character_id: str) -> RegistryEntry | None:
        return next((entry for entry in self.entries if entry.id == character_id), None)


def build_registry(store: Store, collection_id: str) -> Registry:
    """Read the collection's registry, with where each character appears.

    Characters known to the collection but found in no current snapshot are still listed,
    with no appearances. They are in the registry because some reading once put them there,
    and dropping them would quietly narrow the cast the next resolution matches against.
    """
    collection = store.get_collection(collection_id)
    if collection is None:
        raise RegistryError(f"unknown collection {collection_id!r}")

    works = store.list_works(collection_id)
    found: dict[str, list[Appearance]] = {}
    unanalysed: list[tuple[str, str | None]] = []

    # `.get`, not `[]`: a store made before **6.4** has no such column, and the migration
    # story is that an older project gains columns rather than being rewritten.
    def edition_of(work: dict[str, Any]) -> str | None:
        found_edition = work.get("edition")
        return str(found_edition) if found_edition else None

    for work in works:
        snapshots = store.list_snapshots(str(work["id"]))
        if not snapshots:
            unanalysed.append((str(work["title"]), edition_of(work)))
            continue

        # The newest reading of this work, and only that one. An earlier snapshot describes a
        # cast that has since been superseded, and reading them all would report a character
        # cut in a later draft as still present.
        latest = snapshots[-1]
        document = latest.document
        degrees: dict[str, int] = {}
        for relation in document.get("relations") or []:
            for end in (relation.get("source"), relation.get("target")):
                if end:
                    degrees[str(end)] = degrees.get(str(end), 0) + 1

        for character in document.get("characters") or []:
            identifier = str(character.get("id"))
            found.setdefault(identifier, []).append(
                Appearance(
                    work_id=str(work["id"]),
                    work_title=str(work["title"]),
                    snapshot_id=latest.id,
                    relations=degrees.get(identifier, 0),
                    edition=edition_of(work),
                )
            )

    entries = [
        RegistryEntry(character=character, appearances=tuple(found.get(character.id, ())))
        for character in store.list_characters(collection_id)
    ]

    # Characters spanning most works first, then by name: the reader of a shared-universe
    # registry is looking for who carries across it, and that is the ordering of that
    # question rather than of the alphabet.
    entries.sort(key=lambda entry: (-len(entry.appearances), entry.name))

    return Registry(
        collection_id=collection_id,
        collection_name=str(collection["name"]),
        entries=tuple(entries),
        works=tuple((str(work["id"]), str(work["title"]), edition_of(work)) for work in works),
        decisions=tuple(store.list_registry_decisions(collection_id)),
        retired=tuple(
            character
            for character in store.list_characters(collection_id, include_retired=True)
            if character.retired
        ),
        unanalysed=tuple(unanalysed),
    )


def as_json(registry: Registry) -> dict[str, object]:
    """The registry as a document, for the API and for anything storing or showing it."""
    return {
        "collection": {"id": registry.collection_id, "name": registry.collection_name},
        "works": [
            {"id": work_id, "title": title, "edition": edition}
            for work_id, title, edition in registry.works
        ],
        "unanalysed": [
            {"title": title, "edition": edition} for title, edition in registry.unanalysed
        ],
        "decisions": [
            {
                "action": decision.action,
                "source_id": decision.source_id,
                "target_id": decision.target_id,
                "forms": list(decision.forms),
                "note": decision.note,
                "decided_at": decision.decided_at,
            }
            for decision in registry.decisions
        ],
        "retired": [
            {"id": character.id, "name": character.name, "merged_into": character.merged_into}
            for character in registry.retired
        ],
        "characters": [
            {
                "id": entry.id,
                "name": entry.name,
                "kind": entry.character.kind,
                "provenance": entry.character.provenance,
                "aliases": list(entry.character.aliases),
                "spans": entry.spans,
                "appearances": [
                    {
                        "work_id": appearance.work_id,
                        "work_title": appearance.work_title,
                        "edition": appearance.edition,
                        "snapshot_id": appearance.snapshot_id,
                        "relations": appearance.relations,
                    }
                    for appearance in entry.appearances
                ],
            }
            for entry in registry.entries
        ],
    }

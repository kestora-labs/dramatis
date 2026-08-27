"""Reading a Dramatis document somebody else produced (**6.3**).

Invariant 8 says the schema is a separately versioned, published artifact and that *"other
tools should be able to emit and consume Dramatis JSON without running Dramatis"*. Publishing
a schema is a claim; this is the part that tests it. A document that validates must be
readable into a project and behave there like one Dramatis produced itself — otherwise the
schema is a description of an internal format with a URL on it.

The module is deliberately small. Almost everything it needs already exists: `validation`
checks shape and references, `snapshot.save_snapshot` refuses anything the schema rejects and
enforces Invariant 4's immutability, and the registry raises on a surface form two characters
both claim. What is here is the *order* those happen in, and four judgements.

**Nothing is written until everything has been checked.** A `Store` transaction spans one
call, so an import cannot be one atomic write; it can be a refusal that happens before the
first write. Every collision — a snapshot identifier already used for different content, a
character identifier already meaning somebody else, a surface form already claimed — is found
in a pre-flight pass over the whole document. Half an imported reading is worse than none,
because the half that landed looks exactly like a reading somebody meant to have.

**An imported document brings a graph, not the work.** The schema carries a `sha256` per
document and never the text — deliberately, and it is the reason a Dramatis file is safe to
send: it says what was read without shipping somebody's unpublished manuscript. So an
imported snapshot can be opened, diffed, and re-exported with no text present, and its
evidence quotations are readable because they are *in* the snapshot. What it cannot do is
open the surrounding passage, which needs the source.

That is not permanent. A document identifier is derived from its path and its content
(`ids.document_id`), so ingesting the same file later lands on the same identifier and the
text joins the imported graph with nothing to reconcile. The placeholder is a row waiting for
its document, not a wrong answer.

**A document already in the store is never overwritten by a placeholder.** `upsert_document`
sets `content` from what it is given, so importing over an existing document would replace
real text with an empty string — silently, and destroying the only copy in the project. Since
the identifier already implies the path and the content, a row that is there *is* this
document, and the right move is to leave it entirely alone.

**Identity is refused, never guessed.** `char:mary` is one person inside the registry that
minted it (**D64**), and the store makes a character identifier unique across the whole
project. An incoming character whose identifier is already taken by a different name, or
sitting in a different collection, is a collision the importer cannot resolve: merging them
would silently make two people one, and renaming would break the relations that name them.
It refuses and says which. Deciding they are the same person is `dramatis merge`'s job, and
it is a person's judgement (**5.3**).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dramatis.snapshot import SnapshotError, save_snapshot
from dramatis.store import (
    AmbiguousAliasError,
    Document,
    RegisteredCharacter,
    Store,
    TextRevision,
    form_key,
    utc_now,
)
from dramatis.validation import Issue, validate_document


class ImportRefused(Exception):
    """A document was not imported. The message says which rule refused it.

    Not ``ImportError``: that name is a builtin, and shadowing it in a module about importing
    is the kind of joke that costs somebody an afternoon.
    """


@dataclass(frozen=True)
class ImportResult:
    """What an import did, in enough detail to say whether it did what was wanted."""

    snapshot_id: str
    work_id: str
    collection_id: str
    characters: int
    relations: int
    evidence: int

    documents_recorded: int = 0
    """Documents the store did not have, recorded without their text."""

    documents_already_here: int = 0
    """Documents the store already held, left exactly as they were."""

    already_present: bool = False
    """Whether this snapshot was already in the project, making the import a no-op."""

    @property
    def summary(self) -> str:
        # ASCII only, for the reason IngestResult.summary gives: a Windows console under a
        # legacy code page renders typographic punctuation as replacement characters.
        if self.already_present:
            return f"{self.snapshot_id} was already in this project; nothing changed"
        return (
            f"imported {self.snapshot_id}: {self.characters} character(s), "
            f"{self.relations} relation(s), {self.evidence} piece(s) of evidence"
        )


def read_document(path: Path) -> dict[str, Any]:
    """Load a document from disk, with a refusal a person can act on.

    `validate_file` reports a parse failure as an Issue and would do here too, but the caller
    needs the parsed document either way, and reading the file twice to get it is how the two
    copies come to disagree about what is in it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ImportRefused(f"{path} could not be read: {error}") from error

    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ImportRefused(f"{path} is not JSON: {error}") from error

    if not isinstance(document, dict):
        holds = type(document).__name__
        raise ImportRefused(f"{path} is not a Dramatis document: it holds a {holds}")

    return document


def _refuse_invalid(document: dict[str, Any]) -> None:
    issues: list[Issue] = validate_document(document)
    if not issues:
        return
    detail = "; ".join(str(issue) for issue in issues[:5])
    more = f" (and {len(issues) - 5} more)" if len(issues) > 5 else ""
    raise ImportRefused(f"this document does not satisfy the published schema: {detail}{more}")


def _refuse_collisions(store: Store, document: dict[str, Any]) -> bool:
    """Check everything that can refuse, before anything is written.

    Returns whether the snapshot is already present with identical content, which is not a
    collision but the idempotent case: importing the same file twice is a thing people do,
    and it should cost nothing and say so.
    """
    from dramatis.snapshot import document_hash

    snapshot = document["snapshot"]
    collection_id = str(document["collection"]["id"])

    already = False
    existing = store.get_snapshot(str(snapshot["id"]))
    if existing is not None:
        if existing.sha256 != document_hash(document):
            raise ImportRefused(
                f"this project already holds a different {snapshot['id']}. Snapshots are "
                "immutable, so two readings cannot share an identifier."
            )
        already = True

    for character in document.get("characters") or []:
        identifier = str(character["id"])
        held = store.get_character(identifier)
        if held is None:
            continue
        if held.name != character["name"]:
            raise ImportRefused(
                f"{identifier} is already in this project as {held.name!r}, and the document "
                f"calls it {character['name']!r}. Two different people cannot share an "
                "identifier; if they are the same person, say so with `dramatis merge`."
            )
        if held.collection_id != collection_id:
            raise ImportRefused(
                f"{identifier} is already in this project under collection "
                f"{held.collection_id!r}, and the document puts it in {collection_id!r}."
            )

    # Surface forms, checked here as well as in the registry. `upsert_character` raises on a
    # form two characters both claim, but it raises on the *first* one it meets — halfway
    # through writing a cast, with the characters before it already in the store.
    claimed: dict[str, str] = {}
    for character in document.get("characters") or []:
        identifier = str(character["id"])
        for form in (character["name"], *(character.get("aliases") or [])):
            key = form_key(form)
            owner = claimed.get(key)
            if owner is not None and owner != identifier:
                raise ImportRefused(
                    f"the document gives the surface form {form!r} to both {owner} and "
                    f"{identifier}; it cannot denote two characters."
                )
            claimed[key] = identifier

            held_by = store.find_character_by_form(collection_id, form)
            if held_by is not None and held_by.id != identifier:
                raise ImportRefused(
                    f"the surface form {form!r} is already claimed by {held_by.id} in this "
                    f"project, and the document gives it to {identifier}."
                )

    return already


def _character_of(entry: dict[str, Any], collection_id: str) -> RegisteredCharacter:
    return RegisteredCharacter(
        id=str(entry["id"]),
        collection_id=collection_id,
        name=str(entry["name"]),
        kind=str(entry.get("kind") or "unknown"),
        provenance=str(entry["provenance"]),
        review_status=str(entry.get("review_status") or "proposed"),
        notes=entry.get("notes"),
        aliases=tuple(entry.get("aliases") or []),
    )


def import_document(store: Store, document: dict[str, Any]) -> ImportResult:
    """Read a validated Dramatis document into a project.

    Calls no model and reaches no network (Invariant 6). Refuses before writing anything;
    see the module docstring for what it refuses and why.
    """
    _refuse_invalid(document)
    already = _refuse_collisions(store, document)

    collection = document["collection"]
    collection_id = str(collection["id"])
    snapshot = document["snapshot"]
    work_id = str(snapshot["work_id"])

    store.upsert_collection(collection_id, str(collection["name"]), collection.get("description"))

    # Indexed rather than looked up defensively, here and for the revision below: a snapshot
    # naming a work, a revision, or a run the document does not carry is a reference failure,
    # and `_refuse_invalid` has already turned every one of those into a refusal. A second
    # check here would be unreachable code pretending to be a safety net.
    works = {str(entry["id"]): entry for entry in document["works"]}
    work = works[work_id]

    store.upsert_work(
        work_id,
        collection_id,
        str(work["title"]),
        creator=work.get("creator"),
        language=work.get("language"),
        edition=work.get("edition"),
        segment_types=list(work.get("segment_types") or []),
    )

    recorded = 0
    already_here = 0
    for entry in document.get("documents") or []:
        identifier = str(entry["id"])
        if store.get_document(identifier) is not None:
            # Left alone on purpose: a document identifier already implies its path and its
            # content, so the row that is here is this document, and it has the text.
            already_here += 1
            continue
        store.upsert_document(
            Document(
                id=identifier,
                work_id=str(entry["work_id"]),
                role=str(entry["role"]),
                sha256=str(entry.get("sha256") or ""),
                # The schema carries the hash and never the text. See the module docstring:
                # this is a row waiting for its document, not a claim that the file was empty.
                content="",
                title=entry.get("title"),
                path=entry.get("path"),
                media_type=entry.get("media_type"),
            )
        )
        recorded += 1

    revisions = {str(entry["id"]): entry for entry in document["text_revisions"]}
    revision = revisions[str(snapshot["text_revision_id"])]
    store.upsert_text_revision(
        TextRevision(
            id=str(revision["id"]),
            work_id=str(revision["work_id"]),
            sha256=str(revision["sha256"]),
            label=revision.get("label"),
            created_at=str(revision.get("created_at") or utc_now()),
            document_ids=tuple(str(entry) for entry in revision.get("document_ids") or []),
        )
    )

    characters = document.get("characters") or []
    try:
        for entry in characters:
            store.upsert_character(_character_of(entry, collection_id))
    except AmbiguousAliasError as error:  # pragma: no cover - pre-flight should have caught it
        raise ImportRefused(f"the cast could not be registered: {error}") from error

    try:
        save_snapshot(store, document)
    except SnapshotError as error:
        raise ImportRefused(str(error)) from error

    relations = document.get("relations") or []
    return ImportResult(
        snapshot_id=str(snapshot["id"]),
        work_id=work_id,
        collection_id=collection_id,
        characters=len(characters),
        relations=len(relations),
        evidence=sum(len(entry.get("evidence") or []) for entry in (*characters, *relations)),
        documents_recorded=recorded,
        documents_already_here=already_here,
        already_present=already,
    )


def import_file(store: Store, path: Path) -> ImportResult:
    """Read a Dramatis document from disk into a project."""
    return import_document(store, read_document(path))

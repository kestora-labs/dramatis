"""Persistence.

One SQLite file holds a whole project: its texts, and eventually its analyses. A single
portable file is a deliberate feature — a researcher can archive it, send it, or deposit it
without exporting anything, and Invariant 6 means it can be opened later with no API key
and no network.

Document contents are stored in the database rather than referenced on disk. A snapshot
whose evidence cannot be resolved back to the exact text it was drawn from is not evidence,
and a path on somebody's laptop is not a durable reference.

Tables are created as the phases that need them arrive. The DDL is idempotent, so opening
an older store simply adds what is missing.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STORE_VERSION = 3

COLLECTIVES_ARE_ACTORS = "collectives_are_actors"
"""Project setting: whether a group may be a character in its own right (D19).

The name lives here, with the other settings vocabulary, rather than in the module that
acts on it. Ingest records it and analysis reads it; neither should have to import the
other, and ingest in particular must not reach into a module that knows about providers.
"""

DEFAULT_COLLECTIVES_ARE_ACTORS = False
"""Off unless a project says otherwise.

A group reported beside its own members stands as their equal and counts their contacts a
second time, which is what the first live run produced. Corpora where a faction really is
an actor turn it on, and every run records which question was asked.
"""

SETTING_PREFIX = "setting:"
"""Namespace separating a project's settings from the store's own machinery.

Both live in ``meta``, and they are different kinds of thing. ``store_version`` is a fact
about the file that nobody decided; a setting is a decision somebody made about how this
corpus is studied (D17). Listing them together would present the first as though it had
been chosen, and would let a future internal key collide with a setting somebody relies on.
"""

DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS works (
    id            TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id),
    title         TEXT NOT NULL,
    creator       TEXT,
    language      TEXT,
    edition       TEXT,
    segment_types TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    work_id    TEXT NOT NULL REFERENCES works(id),
    title      TEXT,
    path       TEXT,
    role       TEXT NOT NULL CHECK (role IN ('narrative', 'reference')),
    media_type TEXT,
    sha256     TEXT NOT NULL,
    content    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS text_revisions (
    id         TEXT PRIMARY KEY,
    work_id    TEXT NOT NULL REFERENCES works(id),
    label      TEXT,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revision_documents (
    revision_id TEXT NOT NULL REFERENCES text_revisions(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    position    INTEGER NOT NULL,
    PRIMARY KEY (revision_id, document_id)
);

CREATE TABLE IF NOT EXISTS characters (
    id            TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'unknown',
    provenance    TEXT NOT NULL DEFAULT 'observed'
                  CHECK (provenance IN ('observed', 'asserted', 'human')),
    review_status TEXT NOT NULL DEFAULT 'proposed'
                  CHECK (review_status IN ('proposed', 'accepted', 'corrected', 'rejected')),
    notes         TEXT
);

-- One surface form maps to at most one character within a collection. The primary key is
-- the guarantee, not a convention: a form that two characters both claim cannot be stored,
-- so an ambiguous alias is a write failure rather than a silently wrong graph.
CREATE TABLE IF NOT EXISTS character_aliases (
    collection_id TEXT NOT NULL REFERENCES collections(id),
    form_key      TEXT NOT NULL,
    form          TEXT NOT NULL,
    character_id  TEXT NOT NULL REFERENCES characters(id),
    is_canonical  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, form_key)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id                  TEXT PRIMARY KEY,
    model               TEXT NOT NULL,
    provider            TEXT,
    prompt_version      TEXT NOT NULL,
    pipeline_version    TEXT,
    application_version TEXT,
    parameters          TEXT NOT NULL DEFAULT '{}',
    started_at          TEXT,
    completed_at        TEXT
);

-- A snapshot is stored as the rendered document, not as normalised rows. What is kept is
-- exactly what would be exported and cited, so the archived artifact and the published one
-- cannot drift apart. The columns beside it exist for lookup, never as a second source of
-- truth. Snapshots are insert-only: see insert_snapshot.
CREATE TABLE IF NOT EXISTS snapshots (
    id               TEXT PRIMARY KEY,
    work_id          TEXT NOT NULL REFERENCES works(id),
    text_revision_id TEXT NOT NULL REFERENCES text_revisions(id),
    analysis_run_id  TEXT NOT NULL REFERENCES analysis_runs(id),
    label            TEXT,
    schema_version   TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    document         TEXT NOT NULL
);

-- A confirmed structure map: what somebody said each document of a folder is (4.2).
-- Keyed by folder and relative path because that pair is a document's identity before it
-- has been ingested, and the point of saving is to be asked once rather than every run.
-- Region boundaries live inside the plan as quotations, so an edited document re-anchors.
CREATE TABLE IF NOT EXISTS structure_map (
    root         TEXT NOT NULL,
    path         TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    plan         TEXT NOT NULL,
    PRIMARY KEY (root, path)
);

CREATE INDEX IF NOT EXISTS ix_snapshots_work ON snapshots(work_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_revision ON snapshots(text_revision_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_run ON snapshots(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_works_collection ON works(collection_id);
CREATE INDEX IF NOT EXISTS ix_documents_work ON documents(work_id);
CREATE INDEX IF NOT EXISTS ix_revisions_work ON text_revisions(work_id);
CREATE INDEX IF NOT EXISTS ix_characters_collection ON characters(collection_id);
CREATE INDEX IF NOT EXISTS ix_aliases_character ON character_aliases(character_id);
"""


@dataclass(frozen=True)
class Document:
    id: str
    work_id: str
    role: str
    sha256: str
    content: str
    title: str | None = None
    path: str | None = None
    media_type: str | None = None


@dataclass(frozen=True)
class TextRevision:
    id: str
    work_id: str
    sha256: str
    created_at: str
    label: str | None = None
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegisteredCharacter:
    """A character in a collection's registry, with every surface form that denotes it."""

    id: str
    collection_id: str
    name: str
    kind: str = "unknown"
    provenance: str = "observed"
    review_status: str = "proposed"
    notes: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def surface_forms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class StoredSnapshot:
    """A snapshot as kept on disk: the document itself, plus what it is bound to."""

    id: str
    work_id: str
    text_revision_id: str
    analysis_run_id: str
    schema_version: str
    sha256: str
    created_at: str
    document: dict[str, Any]
    label: str | None = None


class AmbiguousAliasError(Exception):
    """A surface form was claimed by two characters in one collection."""


class ImmutableSnapshotError(Exception):
    """An attempt was made to change a snapshot that already exists."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def form_key(form: str) -> str:
    """Normalise a surface form for lookup.

    Case and surrounding whitespace are not meaningful distinctions between two writings
    of the same name. Nothing else is folded — punctuation and internal spacing stay, so
    "St. John" and "St John" remain different forms for a human to reconcile rather than
    being merged by a rule that would be wrong in some other language.
    """
    return " ".join(form.split()).casefold()


def _setting_key(name: str) -> str:
    """Namespace a setting name, refusing one that would escape the namespace.

    A name arriving with the prefix already on it is a caller who thinks they are passing a
    raw key; honouring it would write ``setting:setting:x``, and refusing costs nothing.
    """
    if not name or name.strip() != name:
        raise ValueError(f"a setting name must be non-empty and unpadded, not {name!r}")
    if name.startswith(SETTING_PREFIX):
        raise ValueError(f"pass the setting name, not the stored key: {name!r}")
    return SETTING_PREFIX + name


class Store:
    """A Dramatis project file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    # -- lifecycle ----------------------------------------------------------------------

    def open(self) -> Store:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(DDL)
        self._connection.execute(
            "INSERT INTO meta (key, value) VALUES ('store_version', ?) ON CONFLICT(key) DO NOTHING",
            (str(STORE_VERSION),),
        )
        self._connection.commit()
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Store:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("store is not open; use `with Store(path) as store:`")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connection
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        connection.commit()

    @property
    def store_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = 'store_version'"
        ).fetchone()
        return int(row["value"])

    # -- settings -----------------------------------------------------------------------
    #
    # A project holds the terms it is studied under, not only its data (D17). Values are
    # JSON so a setting reads back as the type it was written as: a switch stored as the
    # string "false" is true, and would be a quiet way to analyse a corpus the wrong way
    # round.
    #
    # Whether a particular setting may be changed after it is set is policy, and belongs
    # with the setting rather than here. This layer stores and retrieves.

    def get_setting(self, name: str, default: Any = None) -> Any:
        """Return a setting, or ``default`` when it has never been set.

        A project predating a setting is indistinguishable here from one that never chose;
        a caller that needs to tell them apart should ask ``settings()`` for what is
        actually recorded, and a run that needs to be reproducible must record the value it
        used rather than the default it might get next time.
        """
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (_setting_key(name),)
        ).fetchone()
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, name: str, value: Any) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_setting_key(name), json.dumps(value)),
            )

    def settings(self) -> dict[str, Any]:
        """Every setting this project records, without the store's own keys."""
        rows = self.connection.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (SETTING_PREFIX + "%",),
        ).fetchall()
        return {row["key"][len(SETTING_PREFIX) :]: json.loads(row["value"]) for row in rows}

    # -- the structure map --------------------------------------------------------------

    def save_structure_map(self, root: str, plans: Mapping[str, Any], confirmed_at: str) -> None:
        """Record what somebody confirmed a folder's documents are.

        Overwrites per document rather than replacing the folder wholesale, so correcting one
        answer does not silently discard the others, and a document dropped from the folder
        keeps its answer for when it comes back.
        """
        with self.transaction() as connection:
            connection.executemany(
                "INSERT INTO structure_map (root, path, confirmed_at, plan) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(root, path) DO UPDATE SET "
                "confirmed_at = excluded.confirmed_at, plan = excluded.plan",
                [
                    (root, path, confirmed_at, json.dumps(plan, ensure_ascii=False))
                    for path, plan in plans.items()
                ],
            )

    def structure_map(self, root: str) -> dict[str, Any]:
        """Every confirmed answer for a folder, by relative path.

        Empty is a real answer and the common one: nobody has been asked yet.
        """
        rows = self.connection.execute(
            "SELECT path, plan FROM structure_map WHERE root = ? ORDER BY path", (root,)
        ).fetchall()
        return {row["path"]: json.loads(row["plan"]) for row in rows}

    def forget_structure_map(self, root: str) -> int:
        """Drop a folder's confirmed answers, so it is asked about again. Returns the count."""
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM structure_map WHERE root = ?", (root,))
            return cursor.rowcount

    # -- writes -------------------------------------------------------------------------

    def upsert_collection(self, identifier: str, name: str, description: str | None = None) -> str:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO collections (id, name, description) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "description = COALESCE(excluded.description, collections.description)",
                (identifier, name, description),
            )
        return identifier

    def upsert_work(
        self,
        identifier: str,
        collection_id: str,
        title: str,
        *,
        creator: str | None = None,
        language: str | None = None,
        edition: str | None = None,
        segment_types: list[str] | None = None,
    ) -> str:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO works (id, collection_id, title, creator, language, edition, "
                "segment_types) VALUES (?, ?, ?, ?, ?, ?, ?) "
                # collection_id is updated, so a caller naming a collection explicitly can
                # move a work. Callers must therefore pass the work's existing collection
                # when they do not mean to move it — see ingest_file.
                "ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                "collection_id = excluded.collection_id, "
                "creator = COALESCE(excluded.creator, works.creator), "
                "language = COALESCE(excluded.language, works.language), "
                "edition = COALESCE(excluded.edition, works.edition), "
                "segment_types = excluded.segment_types",
                (
                    identifier,
                    collection_id,
                    title,
                    creator,
                    language,
                    edition,
                    json.dumps(segment_types or []),
                ),
            )
        return identifier

    def upsert_document(self, document: Document) -> str:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO documents (id, work_id, title, path, role, media_type, sha256, "
                "content) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title = excluded.title, path = excluded.path, "
                "role = excluded.role, media_type = excluded.media_type, "
                "sha256 = excluded.sha256, content = excluded.content",
                (
                    document.id,
                    document.work_id,
                    document.title,
                    document.path,
                    document.role,
                    document.media_type,
                    document.sha256,
                    document.content,
                ),
            )
        return document.id

    def upsert_text_revision(self, revision: TextRevision) -> str:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO text_revisions (id, work_id, label, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET label = COALESCE(excluded.label, "
                "text_revisions.label)",
                (
                    revision.id,
                    revision.work_id,
                    revision.label,
                    revision.sha256,
                    revision.created_at,
                ),
            )
            connection.execute(
                "DELETE FROM revision_documents WHERE revision_id = ?", (revision.id,)
            )
            connection.executemany(
                "INSERT INTO revision_documents (revision_id, document_id, position) "
                "VALUES (?, ?, ?)",
                [
                    (revision.id, document_id, position)
                    for position, document_id in enumerate(revision.document_ids)
                ],
            )
        return revision.id

    # -- reads --------------------------------------------------------------------------

    def get_document(self, identifier: str) -> Document | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE id = ?", (identifier,)
        ).fetchone()
        return None if row is None else Document(**dict(row))

    def get_text_revision(self, identifier: str) -> TextRevision | None:
        row = self.connection.execute(
            "SELECT * FROM text_revisions WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        members = self.connection.execute(
            "SELECT document_id FROM revision_documents WHERE revision_id = ? ORDER BY position",
            (identifier,),
        ).fetchall()
        return TextRevision(
            **dict(row), document_ids=tuple(member["document_id"] for member in members)
        )

    def get_collection(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM collections WHERE id = ?", (identifier,)
        ).fetchone()
        return None if row is None else dict(row)

    def list_collections(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM collections ORDER BY name, id").fetchall()
        return [dict(row) for row in rows]

    def list_works(self, collection_id: str | None = None) -> list[dict[str, Any]]:
        if collection_id is None:
            rows = self.connection.execute("SELECT id FROM works ORDER BY title, id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id FROM works WHERE collection_id = ? ORDER BY title, id",
                (collection_id,),
            ).fetchall()
        found = [self.get_work(row["id"]) for row in rows]
        return [work for work in found if work is not None]

    def count(self, table: str) -> int:
        """Row count for one of the store's own tables."""
        allowed = {
            "collections",
            "works",
            "documents",
            "text_revisions",
            "characters",
            "character_aliases",
            "analysis_runs",
            "snapshots",
        }
        if table not in allowed:
            raise ValueError(f"unknown table {table!r}")
        return int(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    def get_work(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM works WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            return None
        work = dict(row)
        work["segment_types"] = json.loads(work["segment_types"])
        return work

    def list_text_revisions(self, work_id: str) -> list[TextRevision]:
        """Every revision of a work, oldest first.

        Ties on ``created_at`` are broken by insertion order rather than by identifier. Two
        revisions ingested in the same second are common — a folder of drafts read one after
        another — and a revision identifier is a content hash, so ordering by it puts the
        drafts in an order decided by hashing. "Second draft" listed above "First draft" is
        not a cosmetic complaint once the list is what a reader uses to follow the work.
        """
        rows = self.connection.execute(
            "SELECT id FROM text_revisions WHERE work_id = ? ORDER BY created_at, rowid",
            (work_id,),
        ).fetchall()
        revisions = [self.get_text_revision(row["id"]) for row in rows]
        return [revision for revision in revisions if revision is not None]

    # -- character registry -------------------------------------------------------------

    def upsert_character(self, character: RegisteredCharacter) -> str:
        """Write a character and claim its surface forms.

        Raises AmbiguousAliasError if any form is already claimed by a different
        character. Resolution is expected to have settled that; reaching here with a
        conflict means a bug, not a judgement call.
        """
        forms = [
            (form_key(form), form, index == 0) for index, form in enumerate(character.surface_forms)
        ]

        with self.transaction() as connection:
            for key, form, _ in forms:
                row = connection.execute(
                    "SELECT character_id FROM character_aliases "
                    "WHERE collection_id = ? AND form_key = ?",
                    (character.collection_id, key),
                ).fetchone()
                if row is not None and row["character_id"] != character.id:
                    raise AmbiguousAliasError(
                        f"the surface form {form!r} is already claimed by "
                        f"{row['character_id']!r}; it cannot also denote {character.id!r}"
                    )

            connection.execute(
                "INSERT INTO characters (id, collection_id, name, kind, provenance, "
                "review_status, notes) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, "
                "provenance = excluded.provenance, review_status = excluded.review_status, "
                "notes = COALESCE(excluded.notes, characters.notes)",
                (
                    character.id,
                    character.collection_id,
                    character.name,
                    character.kind,
                    character.provenance,
                    character.review_status,
                    character.notes,
                ),
            )
            connection.execute(
                "DELETE FROM character_aliases WHERE character_id = ?", (character.id,)
            )
            connection.executemany(
                "INSERT INTO character_aliases (collection_id, form_key, form, character_id, "
                "is_canonical) VALUES (?, ?, ?, ?, ?)",
                [
                    (character.collection_id, key, form, character.id, int(canonical))
                    for key, form, canonical in forms
                ],
            )
        return character.id

    def get_character(self, identifier: str) -> RegisteredCharacter | None:
        row = self.connection.execute(
            "SELECT * FROM characters WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        aliases = self.connection.execute(
            "SELECT form FROM character_aliases WHERE character_id = ? AND is_canonical = 0 "
            "ORDER BY form",
            (identifier,),
        ).fetchall()
        return RegisteredCharacter(**dict(row), aliases=tuple(a["form"] for a in aliases))

    def find_character_by_form(self, collection_id: str, form: str) -> RegisteredCharacter | None:
        """Resolve a surface form to the character that claims it, if any."""
        row = self.connection.execute(
            "SELECT character_id FROM character_aliases WHERE collection_id = ? AND form_key = ?",
            (collection_id, form_key(form)),
        ).fetchone()
        return None if row is None else self.get_character(row["character_id"])

    def list_characters(self, collection_id: str) -> list[RegisteredCharacter]:
        rows = self.connection.execute(
            "SELECT id FROM characters WHERE collection_id = ? ORDER BY name, id",
            (collection_id,),
        ).fetchall()
        found = [self.get_character(row["id"]) for row in rows]
        return [character for character in found if character is not None]

    # -- analyses and snapshots ---------------------------------------------------------

    def upsert_analysis_run(self, run: dict[str, Any]) -> str:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO analysis_runs (id, model, provider, prompt_version, "
                "pipeline_version, application_version, parameters, started_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "completed_at = COALESCE(excluded.completed_at, analysis_runs.completed_at)",
                (
                    run["id"],
                    run["model"],
                    run.get("provider"),
                    run["prompt_version"],
                    run.get("pipeline_version"),
                    run.get("application_version"),
                    json.dumps(run.get("parameters") or {}, sort_keys=True),
                    run.get("started_at"),
                    run.get("completed_at"),
                ),
            )
        return run["id"]

    def get_analysis_run(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["parameters"] = json.loads(run["parameters"])
        return run

    def list_analysis_runs(self, work_id: str) -> list[dict[str, Any]]:
        """Every analysis run that produced a snapshot of this work, oldest first.

        A run is not owned by a work — the same run configuration could be applied to
        several — so this asks the snapshots which runs a work has actually been through.
        Ordered by when the run started rather than when its snapshot was written, because
        the question this answers is how the *analysis* moved, and two snapshots recorded
        minutes apart may come from runs a month apart.
        """
        rows = self.connection.execute(
            "SELECT r.id FROM snapshots s "
            "JOIN analysis_runs r ON r.id = s.analysis_run_id "
            "WHERE s.work_id = ? GROUP BY r.id ORDER BY r.started_at, r.rowid",
            (work_id,),
        ).fetchall()
        found = [self.get_analysis_run(row["id"]) for row in rows]
        return [run for run in found if run is not None]

    def insert_snapshot(self, snapshot: StoredSnapshot) -> str:
        """Write a snapshot. Snapshots are immutable (Invariant 4).

        Writing the same identifier with identical content is a no-op, so a repeated run is
        harmless. Writing it with *different* content raises: a snapshot whose meaning
        changed under a citation that already names it is the failure this prevents.
        """
        existing = self.connection.execute(
            "SELECT sha256 FROM snapshots WHERE id = ?", (snapshot.id,)
        ).fetchone()
        if existing is not None:
            if existing["sha256"] == snapshot.sha256:
                return snapshot.id
            raise ImmutableSnapshotError(
                f"snapshot {snapshot.id!r} already exists with different content. "
                "Snapshots are immutable — a new analysis is a new snapshot."
            )

        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO snapshots (id, work_id, text_revision_id, analysis_run_id, "
                "label, schema_version, sha256, created_at, document) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.id,
                    snapshot.work_id,
                    snapshot.text_revision_id,
                    snapshot.analysis_run_id,
                    snapshot.label,
                    snapshot.schema_version,
                    snapshot.sha256,
                    snapshot.created_at,
                    json.dumps(snapshot.document, sort_keys=True, ensure_ascii=False),
                ),
            )
        return snapshot.id

    def get_snapshot(self, identifier: str) -> StoredSnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["document"] = json.loads(record["document"])
        return StoredSnapshot(**record)

    def list_snapshots(self, work_id: str) -> list[StoredSnapshot]:
        """Every snapshot of a work, oldest first.

        Ties on ``created_at`` break on insertion order, for the reason
        ``list_text_revisions`` gives: a snapshot identifier is a content hash, so ordering
        by it puts two snapshots written in the same second into an order decided by
        hashing. 3.4 reads this order to decide which of two snapshots a diff runs *from*,
        and a diff run backwards reports every strengthening as a weakening.
        """
        rows = self.connection.execute(
            "SELECT id FROM snapshots WHERE work_id = ? ORDER BY created_at, rowid", (work_id,)
        ).fetchall()
        found = [self.get_snapshot(row["id"]) for row in rows]
        return [snapshot for snapshot in found if snapshot is not None]

    def revision_text(self, revision_id: str) -> str:
        """Return the full text of a revision, documents concatenated in order."""
        rows = self.connection.execute(
            "SELECT d.content FROM revision_documents rd "
            "JOIN documents d ON d.id = rd.document_id "
            "WHERE rd.revision_id = ? ORDER BY rd.position",
            (revision_id,),
        ).fetchall()
        return "".join(row["content"] for row in rows)

    def revision_document_spans(self, revision_id: str) -> list[tuple[int, int, str]]:
        """Where each document sits inside the text ``revision_text`` returns.

        Kept next to the concatenation rather than derived by a caller, because the two have
        to agree about the joining and a second implementation of "documents, in order, end
        to end" is a second place for that to be got wrong. Returns ``(start, end, id)`` with
        ``end`` exclusive, in position order.
        """
        rows = self.connection.execute(
            "SELECT d.id, d.content FROM revision_documents rd "
            "JOIN documents d ON d.id = rd.document_id "
            "WHERE rd.revision_id = ? ORDER BY rd.position",
            (revision_id,),
        ).fetchall()

        spans: list[tuple[int, int, str]] = []
        cursor = 0
        for row in rows:
            length = len(row["content"])
            spans.append((cursor, cursor + length, row["id"]))
            cursor += length
        return spans

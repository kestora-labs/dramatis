"""Persistence.

One SQLite file holds a whole project: its texts, and eventually its analyses. A single
portable file is a deliberate feature — a researcher can archive it, send it, or deposit it
without exporting anything, and Invariant 6 means it can be opened later with no API key
and no network.

**Postgres is the alternative, chosen by pointing at a URL instead of a file** (**4.10**).
Nothing in this module knows which is in use: `dramatis.drivers` rewrites what differs on the
way out, so the queries below are written once. SQLite remains the default and the shape the
project is designed around.

Document contents are stored in the database rather than referenced on disk. A snapshot
whose evidence cannot be resolved back to the exact text it was drawn from is not evidence,
and a path on somebody's laptop is not a durable reference.

Tables are created as the phases that need them arrive. The DDL is idempotent, so opening
an older store simply adds what is missing — but only *tables*: `CREATE TABLE IF NOT EXISTS`
leaves a table that already exists exactly as it was, columns included. A column added to an
existing table therefore needs `ADDED_COLUMNS` as well, or every project file made before it
fails on the first query that names it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dramatis import ids
from dramatis.drivers import Connection, driver_for, is_postgres

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

NARRATIVE = "narrative"
REFERENCE = "reference"
DOCUMENT_ROLES = (NARRATIVE, REFERENCE)
"""What a document can be, matching the CHECK constraint on ``documents.role``.

Defined here because the column is the authority: a role is a fact the store enforces, and
three modules spelling the same two strings is three places for one of them to drift from
what the database will accept. `structure` proposes these values, `ingest` records them, and
`pipeline` reads by them (**4.3**).
"""

EXCLUDED = "excluded"
"""A third value a *region* role can take, alongside the two document roles.

Not in ``DOCUMENT_ROLES`` and not a value the ``documents.role`` column will accept: an
excluded region is not a kind of document but a span of one that ingest drops (**4.11**) — a
critical preface, a transcriber's note, an appendix. It lives only in the structure map's
JSON. It is here, beside the roles it stands with, so `structure` and `ingest` spell it the
same way. A model never proposes it; throwing text away is a person's call.
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
    notes         TEXT,
    -- Set when a person merged this character into another (5.3). The row stays rather than
    -- being deleted: snapshots already written name this identifier, and a reader tracing one
    -- back is owed an answer better than nothing. A retired character holds no surface forms,
    -- so resolution can never assign to it again without anything having to check.
    merged_into   TEXT REFERENCES characters(id)
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

-- What a person decided about one claim in one reading (5.1). Append-only: a row is a
-- decision, and a subject's current status is its newest row. Overwriting in place would
-- lose that somebody once accepted what has since been rejected, and a review whose earlier
-- state cannot be recovered is exactly the silent overwrite phase 5 exists to prevent.
--
-- Keyed by work rather than by snapshot. A judgement is about a claim, not about the
-- document that happened to carry it: the same character is the same character in the next
-- reading of the same work, and a decision that expired whenever the analysis re-ran would
-- have to be made again every time. The snapshot the decision was taken in is recorded
-- beside it, because what a person was looking at is part of what they decided.
CREATE TABLE IF NOT EXISTS reviews (
    work_id      TEXT NOT NULL REFERENCES works(id),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('character', 'relation')),
    subject_id   TEXT NOT NULL,
    status       TEXT NOT NULL
                 CHECK (status IN ('proposed', 'accepted', 'corrected', 'rejected')),
    note         TEXT,
    snapshot_id  TEXT NOT NULL REFERENCES snapshots(id),
    decided_at   TEXT NOT NULL
);

-- A person's replacement for one field of one node or edge (5.2). Append-only and newest
-- wins, for the reason `reviews` is: a correction that quietly replaced its predecessor would
-- lose the fact that somebody had already been here and decided otherwise.
--
-- `was` is what the reading said at the moment the correction was made. It is not decoration:
-- it is the only way a later analysis can be asked whether it still says the same thing, and
-- so the only way a disagreement can be noticed rather than swallowed.
--
-- One row per field rather than per subject. Correcting a name and correcting a note are two
-- decisions, and folding them into one record would make the second silently discard the
-- first.
CREATE TABLE IF NOT EXISTS corrections (
    work_id      TEXT NOT NULL REFERENCES works(id),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('character', 'relation')),
    subject_id   TEXT NOT NULL,
    field        TEXT NOT NULL,
    value        TEXT NOT NULL,
    was          TEXT NOT NULL,
    note         TEXT,
    snapshot_id  TEXT NOT NULL REFERENCES snapshots(id),
    corrected_at TEXT NOT NULL
);

-- Where a later reading proposed something other than what a correction replaced (5.2).
-- The correction still stands — a person outranks a run — but the run's competing claim is
-- written down rather than dropped. That is the whole of "never silently overwritten": the
-- human value is not lost to the analysis, and the analysis is not lost to the human value.
CREATE TABLE IF NOT EXISTS correction_conflicts (
    work_id      TEXT NOT NULL REFERENCES works(id),
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    field        TEXT NOT NULL,
    proposed     TEXT NOT NULL,
    held         TEXT NOT NULL,
    snapshot_id  TEXT NOT NULL REFERENCES snapshots(id),
    noticed_at   TEXT NOT NULL
);

-- What a person decided about who is who (5.3). Both a merge and a split are one act — a set
-- of surface forms moving from one character to another — so one shape holds both. A merge
-- empties its source and retires it; a split creates its target and leaves the source
-- standing.
--
-- Recorded because the registry alone cannot say it. After a merge the registry shows one
-- character answering to both names, which is the outcome and not the decision; without this
-- nobody could tell a curated identity from one the model proposed that way, and the merge is
-- the more consequential of the two.
CREATE TABLE IF NOT EXISTS registry_decisions (
    collection_id TEXT NOT NULL REFERENCES collections(id),
    action        TEXT NOT NULL CHECK (action IN ('merge', 'split')),
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    forms         TEXT NOT NULL,
    note          TEXT,
    decided_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_snapshots_work ON snapshots(work_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_revision ON snapshots(text_revision_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_run ON snapshots(analysis_run_id);
CREATE INDEX IF NOT EXISTS ix_works_collection ON works(collection_id);
CREATE INDEX IF NOT EXISTS ix_documents_work ON documents(work_id);
CREATE INDEX IF NOT EXISTS ix_revisions_work ON text_revisions(work_id);
CREATE INDEX IF NOT EXISTS ix_characters_collection ON characters(collection_id);
CREATE INDEX IF NOT EXISTS ix_aliases_character ON character_aliases(character_id);
CREATE INDEX IF NOT EXISTS ix_reviews_subject ON reviews(work_id, subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS ix_corrections_subject ON corrections(work_id, subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS ix_conflicts_snapshot ON correction_conflicts(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_decisions_collection ON registry_decisions(collection_id);
"""


ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("characters", "merged_into", "TEXT REFERENCES characters(id)"),
)
"""Columns added to tables that already existed, as (table, column, definition).

The DDL above is what a *new* store is built from; this is what an older one is brought up
to. Both are needed, and the pair has to agree — a test asserts that every column named here
is in the DDL too, so a column can never be added for new stores and forgotten for old ones.

Additive only. A column is added with no default and no backfill, so an older store gains it
holding NULL, which is what "nobody has decided this yet" means for every column here.
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

    merged_into: str | None = None
    """The character this one was merged into, where somebody merged it (**5.3**).

    A retired character keeps its row and loses its surface forms. It is excluded from the
    registry a reading resolves against, so nothing can be assigned to it again, but it stays
    answerable: a snapshot written before the merge names this identifier, and a reader
    following it back deserves to be told where the character went.
    """

    @property
    def retired(self) -> bool:
        return self.merged_into is not None

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


@dataclass(frozen=True)
class ReviewDecision:
    """One human judgement about one node or edge, as kept on disk (**5.1**).

    ``snapshot_id`` is the reading the decision was taken in, not the thing it applies to:
    the judgement is about the subject, and the snapshot records what was on the screen when
    it was made. See the ``reviews`` table for why those are different.
    """

    work_id: str
    subject_kind: str
    subject_id: str
    status: str
    snapshot_id: str
    decided_at: str
    note: str | None = None


@dataclass(frozen=True)
class Correction:
    """One human replacement for one field of one node or edge, as kept on disk (**5.2**).

    ``value`` and ``was`` are the decoded values, not JSON text — a list of aliases is a list
    here and in the document it will be written into. The store encodes them on the way down
    so that a correction reads back as the type it was made as; a number stored as a string
    would reach the schema as a string and be rejected there rather than here.
    """

    work_id: str
    subject_kind: str
    subject_id: str
    field: str
    value: Any
    was: Any
    """What the reading said when the correction was made, so a later one can be asked
    whether it still says it."""

    snapshot_id: str
    corrected_at: str
    note: str | None = None


@dataclass(frozen=True)
class CorrectionConflict:
    """A later reading proposing something other than what a correction replaced (**5.2**)."""

    work_id: str
    subject_kind: str
    subject_id: str
    field: str
    proposed: Any
    """What the new analysis said."""

    held: Any
    """The human value that stood in spite of it."""

    snapshot_id: str
    noticed_at: str


@dataclass(frozen=True)
class RegistryDecision:
    """One person's ruling about who is who (**5.3**).

    A merge and a split are the same shape: surface forms moving from ``source_id`` to
    ``target_id``. A merge empties its source and retires it; a split creates its target and
    leaves the source standing.
    """

    collection_id: str
    action: str
    source_id: str
    target_id: str
    forms: tuple[str, ...]
    decided_at: str
    note: str | None = None


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
    """A Dramatis project: one SQLite file, or a Postgres database named by URL.

    The backend is chosen by what it is pointed at and is invisible above this class — the
    queries below are written once and rewritten for the driver on the way out (**4.10**).
    """

    def __init__(self, path: Path | str) -> None:
        # A Postgres URL is kept as the string it is; a file becomes a Path. `self.path` is
        # what every message shows the user, so it must read back as what they typed.
        self._driver = driver_for(path)
        self.path = path if is_postgres(path) else Path(path)
        self._connection: Connection | None = None

    @property
    def backend(self) -> str:
        """Which database this store speaks to, for anything that reports on itself."""
        return self._driver.name

    # -- lifecycle ----------------------------------------------------------------------

    def open(self) -> Store:
        self._connection = Connection(self._driver.connect(self.path), self._driver)
        self._driver.prepare(self._connection._raw)
        self._connection.executescript(DDL)
        self._add_missing_columns()
        self._connection.execute(
            "INSERT INTO meta (key, value) VALUES ('store_version', ?) ON CONFLICT(key) DO NOTHING",
            (str(STORE_VERSION),),
        )
        self._connection.commit()
        return self

    def _add_missing_columns(self) -> None:
        """Bring a store made before a column existed up to the current schema.

        Runs on every open and does nothing on a store that is already current, which is the
        same bargain the DDL makes. Adding the column is the whole migration: every one of
        them means "nobody has decided this", and NULL says that already.
        """
        connection = self.connection
        for table, column, definition in ADDED_COLUMNS:
            if column in connection.columns(table):
                continue
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> Store:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("store is not open; use `with Store(path) as store:`")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
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
        # Named rather than taken positionally: a Postgres row is a mapping, and `row[0]`
        # raises a KeyError on it. Naming the column works on both backends (**4.10**).
        row = self.connection.execute(f"SELECT count(*) AS tally FROM {table}").fetchone()
        return int(row["tally"])

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
            "SELECT id FROM text_revisions WHERE work_id = ? ORDER BY created_at, {tiebreak}",
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
                # `merged_into` is deliberately not in the update list: a reading writes
                # characters on every run and must never un-retire one a person merged away.
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

    def list_characters(
        self, collection_id: str, *, include_retired: bool = False
    ) -> list[RegisteredCharacter]:
        """The collection's cast.

        A character somebody merged into another is left out by default: it holds no surface
        forms, nothing can resolve to it, and listing it would put a person with no part in
        the work in front of every reader of the registry. Callers tracing an identifier from
        an older snapshot ask for it back.
        """
        query = "SELECT id FROM characters WHERE collection_id = ?"
        if not include_retired:
            query += " AND merged_into IS NULL"
        rows = self.connection.execute(query + " ORDER BY name, id", (collection_id,)).fetchall()
        found = [self.get_character(row["id"]) for row in rows]
        return [character for character in found if character is not None]

    # -- who is who ---------------------------------------------------------------------
    #
    # Merging and splitting both move surface forms between characters, and both have to land
    # whole: a form belongs to exactly one character, so a half-applied move leaves a name
    # denoting nobody. `rewrite_characters` is the one write that can do it. Deciding which
    # forms move, and whether the move is a merge or a split, is `dramatis.identity`'s.

    def rewrite_characters(
        self,
        characters: Sequence[RegisteredCharacter],
        *,
        retire: Mapping[str, str] | None = None,
    ) -> None:
        """Rewrite several characters and their claims together, in one transaction.

        `upsert_character` checks each form against every claim in the collection, which is
        right when a reading is adding to the registry and wrong here: a merge hands a form
        from one character to another, and checking part-way through would refuse a move for
        colliding with the character it is moving away from. The check is made against claims
        held *outside* this batch instead, so the batch may shuffle forms among itself while
        still being unable to steal one from a character it does not name.

        ``retire`` maps a character to the one that absorbed it. A retired character keeps its
        row and, having handed over its forms, holds none.
        """
        retire = dict(retire or {})
        batch = {character.id for character in characters} | set(retire)

        with self.transaction() as connection:
            outside = {
                row["form_key"]: row["character_id"]
                for row in connection.execute(
                    "SELECT form_key, character_id FROM character_aliases WHERE collection_id = ?",
                    (next(iter({c.collection_id for c in characters}), ""),),
                ).fetchall()
                if row["character_id"] not in batch
            }
            for character in characters:
                if character.id in retire:
                    continue
                for form in character.surface_forms:
                    holder = outside.get(form_key(form))
                    if holder is not None:
                        raise AmbiguousAliasError(
                            f"the surface form {form!r} is already claimed by {holder!r}; "
                            f"it cannot also denote {character.id!r}"
                        )

            for identifier in batch:
                connection.execute(
                    "DELETE FROM character_aliases WHERE character_id = ?", (identifier,)
                )

            for character in characters:
                connection.execute(
                    "INSERT INTO characters (id, collection_id, name, kind, provenance, "
                    "review_status, notes, merged_into) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, "
                    "provenance = excluded.provenance, "
                    "review_status = excluded.review_status, "
                    "notes = COALESCE(excluded.notes, characters.notes)",
                    (
                        character.id,
                        character.collection_id,
                        character.name,
                        character.kind,
                        character.provenance,
                        character.review_status,
                        character.notes,
                        character.merged_into,
                    ),
                )
                if character.id in retire:
                    # A retired character claims nothing at all, its own name included: that
                    # name is exactly what it handed over, and leaving it claimed here would
                    # collide with the character that took it. The row keeps the name so a
                    # reader tracing an older snapshot still learns who it was.
                    continue
                connection.executemany(
                    "INSERT INTO character_aliases (collection_id, form_key, form, "
                    "character_id, is_canonical) VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            character.collection_id,
                            form_key(form),
                            form,
                            character.id,
                            int(index == 0),
                        )
                        for index, form in enumerate(character.surface_forms)
                    ],
                )

            for absorbed, survivor in retire.items():
                connection.execute(
                    "UPDATE characters SET merged_into = ? WHERE id = ?", (survivor, absorbed)
                )

    def append_registry_decision(self, decision: RegistryDecision) -> RegistryDecision:
        """Record who a person decided somebody was. Append-only, like every other ruling."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO registry_decisions (collection_id, action, source_id, target_id, "
                "forms, note, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.collection_id,
                    decision.action,
                    decision.source_id,
                    decision.target_id,
                    json.dumps(list(decision.forms), ensure_ascii=False),
                    decision.note,
                    decision.decided_at,
                ),
            )
        return decision

    def list_registry_decisions(self, collection_id: str) -> list[RegistryDecision]:
        """Every merge and split in a collection, oldest first."""
        rows = self.connection.execute(
            "SELECT * FROM registry_decisions WHERE collection_id = ? "
            "ORDER BY decided_at, {tiebreak}",
            (collection_id,),
        ).fetchall()
        return [
            RegistryDecision(
                collection_id=str(row["collection_id"]),
                action=str(row["action"]),
                source_id=str(row["source_id"]),
                target_id=str(row["target_id"]),
                forms=tuple(json.loads(row["forms"])),
                decided_at=str(row["decided_at"]),
                note=row["note"],
            )
            for row in rows
        ]

    def merged_into(self, collection_id: str) -> dict[str, str]:
        """Where each retired character went, followed all the way to a standing one.

        Chains are resolved here rather than by every caller: merging B into A and then A into
        C must leave B pointing at C, or human work recorded against B stops being found the
        moment A is merged on.
        """
        rows = self.connection.execute(
            "SELECT id, merged_into FROM characters "
            "WHERE collection_id = ? AND merged_into IS NOT NULL",
            (collection_id,),
        ).fetchall()
        direct = {str(row["id"]): str(row["merged_into"]) for row in rows}

        settled: dict[str, str] = {}
        for start in direct:
            seen = {start}
            at = direct[start]
            while at in direct and at not in seen:
                seen.add(at)
                at = direct[at]
            settled[start] = at
        return settled

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
            "WHERE s.work_id = ? GROUP BY r.id ORDER BY r.started_at, r.{tiebreak}",
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
            "SELECT id FROM snapshots WHERE work_id = ? ORDER BY created_at, {tiebreak}", (work_id,)
        ).fetchall()
        found = [self.get_snapshot(row["id"]) for row in rows]
        return [snapshot for snapshot in found if snapshot is not None]

    # -- following a merge --------------------------------------------------------------
    #
    # Reviews (5.1) and corrections (5.2) are recorded against an identifier, and 5.3 lets a
    # person change which identifier a character has. Folding the two together here rather
    # than in each reader is the same choice the origin guard made: a caller that has to
    # remember is a caller that will forget, and forgetting means somebody's rejection quietly
    # stops applying the moment two characters are merged.
    #
    # The raw logs are never rewritten. What moves is the answer to "where does this stand
    # now", which is the only question whose answer a merge changes.

    def _collection_of(self, work_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT collection_id FROM works WHERE id = ?", (work_id,)
        ).fetchone()
        return None if row is None else str(row["collection_id"])

    def _redirects_for_work(self, work_id: str) -> dict[str, str]:
        collection_id = self._collection_of(work_id)
        return {} if collection_id is None else self.merged_into(collection_id)

    @staticmethod
    def subject_after_merges(
        redirects: Mapping[str, str], subject_kind: str, subject_id: str
    ) -> str:
        """One review or correction subject, seen through every merge since it was recorded.

        A character redirects directly. A relation redirects through its endpoints, because
        merging one of them changes which pair the edge joins and therefore its identifier —
        so a correction to an edge would otherwise be stranded by a merge at either end.
        """
        if not redirects:
            return subject_id
        if subject_kind == "character":
            return redirects.get(subject_id, subject_id)

        endpoints = ids.relation_endpoints(subject_id)
        if endpoints is None:
            return subject_id
        source, target, provenance = endpoints
        moved = (redirects.get(source, source), redirects.get(target, target))
        # An edge whose two ends became one person is not an edge any more. Left as it was
        # rather than pointed at a self-loop the graph will never contain.
        if moved[0] == moved[1]:
            return subject_id
        return ids.relation_id(moved[0], moved[1], provenance)

    # -- reviews ------------------------------------------------------------------------
    #
    # Append-only, newest wins. Nothing here decides whether a decision is *allowed* — that
    # `corrected` says what it corrected, that the subject is one the reading actually
    # proposed — because those are rules about review rather than about storage, and they
    # live in `dramatis.review`. This layer writes rows and reads them back in order.

    def append_review(self, decision: ReviewDecision) -> ReviewDecision:
        """Record a decision. Never replaces an earlier one; it supersedes it."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO reviews (work_id, subject_kind, subject_id, status, note, "
                "snapshot_id, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.work_id,
                    decision.subject_kind,
                    decision.subject_id,
                    decision.status,
                    decision.note,
                    decision.snapshot_id,
                    decision.decided_at,
                ),
            )
        return decision

    def list_reviews(
        self,
        work_id: str,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> list[ReviewDecision]:
        """Every decision recorded about a work, oldest first.

        Ties on ``decided_at`` break on insertion order, for the reason ``list_snapshots``
        gives: two decisions taken in the same second are still one after the other, and
        which came second is the one that stands.
        """
        query = "SELECT * FROM reviews WHERE work_id = ?"
        parameters: list[str] = [work_id]
        if subject_kind is not None:
            query += " AND subject_kind = ?"
            parameters.append(subject_kind)
        if subject_id is not None:
            query += " AND subject_id = ?"
            parameters.append(subject_id)
        rows = self.connection.execute(
            query + " ORDER BY decided_at, {tiebreak}", parameters
        ).fetchall()
        return [
            ReviewDecision(
                work_id=str(row["work_id"]),
                subject_kind=str(row["subject_kind"]),
                subject_id=str(row["subject_id"]),
                status=str(row["status"]),
                snapshot_id=str(row["snapshot_id"]),
                decided_at=str(row["decided_at"]),
                note=row["note"],
            )
            for row in rows
        ]

    def current_reviews(self, work_id: str) -> dict[tuple[str, str], ReviewDecision]:
        """Where review stands for each subject of a work, keyed by (kind, id).

        Folded here rather than asked of the database, so both backends answer identically
        and the ordering rule ``list_reviews`` documents is applied once.

        Subjects are reported under the identifier they have *now*: a ruling made before a
        merge follows the character that absorbed it (**5.3**). Where both characters had been
        ruled on, the later ruling stands, which is the rule already governing two rulings on
        one subject.
        """
        redirects = self._redirects_for_work(work_id)
        standing: dict[tuple[str, str], ReviewDecision] = {}
        for decision in self.list_reviews(work_id):
            now = self.subject_after_merges(redirects, decision.subject_kind, decision.subject_id)
            standing[(decision.subject_kind, now)] = (
                decision if now == decision.subject_id else replace(decision, subject_id=now)
            )
        return standing

    # -- corrections --------------------------------------------------------------------
    #
    # Append-only, newest wins, exactly as `reviews` is. Nothing here decides which fields may
    # be corrected or what a valid value looks like — those are rules about correction rather
    # than about storage, and they live in `dramatis.correction`. This layer writes rows.

    def append_correction(self, correction: Correction) -> Correction:
        """Record a correction. Never replaces an earlier one; it supersedes it."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO corrections (work_id, subject_kind, subject_id, field, value, "
                "was, note, snapshot_id, corrected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    correction.work_id,
                    correction.subject_kind,
                    correction.subject_id,
                    correction.field,
                    json.dumps(correction.value, ensure_ascii=False),
                    json.dumps(correction.was, ensure_ascii=False),
                    correction.note,
                    correction.snapshot_id,
                    correction.corrected_at,
                ),
            )
        return correction

    def list_corrections(
        self,
        work_id: str,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        field: str | None = None,
    ) -> list[Correction]:
        """Every correction recorded against a work, oldest first.

        Ties on ``corrected_at`` break on insertion order, for the reason ``list_reviews``
        gives: two corrections made in the same second are still one after the other, and
        which came second is the one that stands.
        """
        query = "SELECT * FROM corrections WHERE work_id = ?"
        parameters: list[str] = [work_id]
        for column, wanted in (
            ("subject_kind", subject_kind),
            ("subject_id", subject_id),
            ("field", field),
        ):
            if wanted is not None:
                query += f" AND {column} = ?"
                parameters.append(wanted)
        rows = self.connection.execute(
            query + " ORDER BY corrected_at, {tiebreak}", parameters
        ).fetchall()
        return [
            Correction(
                work_id=str(row["work_id"]),
                subject_kind=str(row["subject_kind"]),
                subject_id=str(row["subject_id"]),
                field=str(row["field"]),
                value=json.loads(row["value"]),
                was=json.loads(row["was"]),
                snapshot_id=str(row["snapshot_id"]),
                corrected_at=str(row["corrected_at"]),
                note=row["note"],
            )
            for row in rows
        ]

    def current_corrections(self, work_id: str) -> dict[tuple[str, str, str], Correction]:
        """The correction that stands for each field of each subject, keyed by (kind, id, field).

        Folded here rather than asked of the database, so both backends answer identically and
        the ordering rule ``list_corrections`` documents is applied once.

        As with reviews, a correction made before a merge follows the character that absorbed
        it (**5.3**), and where both had a correction to one field the later one stands.
        """
        redirects = self._redirects_for_work(work_id)
        standing: dict[tuple[str, str, str], Correction] = {}
        for correction in self.list_corrections(work_id):
            now = self.subject_after_merges(
                redirects, correction.subject_kind, correction.subject_id
            )
            key = (correction.subject_kind, now, correction.field)
            standing[key] = (
                correction if now == correction.subject_id else replace(correction, subject_id=now)
            )
        return standing

    def append_correction_conflicts(self, conflicts: Sequence[CorrectionConflict]) -> int:
        """Record what a reading proposed where a correction overruled it. Returns the count."""
        if not conflicts:
            return 0
        with self.transaction() as connection:
            connection.executemany(
                "INSERT INTO correction_conflicts (work_id, subject_kind, subject_id, field, "
                "proposed, held, snapshot_id, noticed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        conflict.work_id,
                        conflict.subject_kind,
                        conflict.subject_id,
                        conflict.field,
                        json.dumps(conflict.proposed, ensure_ascii=False),
                        json.dumps(conflict.held, ensure_ascii=False),
                        conflict.snapshot_id,
                        conflict.noticed_at,
                    )
                    for conflict in conflicts
                ],
            )
        return len(conflicts)

    def list_correction_conflicts(
        self, work_id: str, *, snapshot_id: str | None = None
    ) -> list[CorrectionConflict]:
        """Disagreements a reading raised with a standing correction, oldest first."""
        query = "SELECT * FROM correction_conflicts WHERE work_id = ?"
        parameters: list[str] = [work_id]
        if snapshot_id is not None:
            query += " AND snapshot_id = ?"
            parameters.append(snapshot_id)
        rows = self.connection.execute(
            query + " ORDER BY noticed_at, {tiebreak}", parameters
        ).fetchall()
        return [
            CorrectionConflict(
                work_id=str(row["work_id"]),
                subject_kind=str(row["subject_kind"]),
                subject_id=str(row["subject_id"]),
                field=str(row["field"]),
                proposed=json.loads(row["proposed"]),
                held=json.loads(row["held"]),
                snapshot_id=str(row["snapshot_id"]),
                noticed_at=str(row["noticed_at"]),
            )
            for row in rows
        ]

    def revision_text(self, revision_id: str, *, roles: Sequence[str] | None = None) -> str:
        """Return the text of a revision, documents concatenated in order.

        ``roles`` narrows it to documents of those roles. Narrative and reference material
        are read by different prompts and yield relations of different provenance (**4.3**),
        so the two are never handed to one analysis as a single run of text.
        """
        return "".join(row["content"] for row in self._revision_rows(revision_id, roles))

    def _revision_rows(self, revision_id: str, roles: Sequence[str] | None) -> list[Any]:
        """Documents of a revision in position order, optionally narrowed by role.

        One query behind both ``revision_text`` and ``revision_document_spans``, because the
        two must agree about which documents are in and in what order. A second
        implementation of "documents, in order, end to end" is a second place to get that
        wrong, and the symptom would be evidence attributed to the wrong document.
        """
        query = (
            "SELECT d.id, d.content FROM revision_documents rd "
            "JOIN documents d ON d.id = rd.document_id "
            "WHERE rd.revision_id = ?"
        )
        parameters: list[str] = [revision_id]
        if roles is not None:
            query += f" AND d.role IN ({', '.join('?' for _ in roles)})"
            parameters.extend(roles)
        return self.connection.execute(query + " ORDER BY rd.position", parameters).fetchall()

    def revision_document_spans(
        self, revision_id: str, *, roles: Sequence[str] | None = None
    ) -> list[tuple[int, int, str]]:
        """Where each document sits inside the text ``revision_text`` returns.

        Kept next to the concatenation rather than derived by a caller, because the two have
        to agree about the joining. Returns ``(start, end, id)`` with ``end`` exclusive, in
        position order. ``roles`` must match whatever was passed to ``revision_text``, or the
        offsets will index a different string than the one they are used against.
        """
        rows = self._revision_rows(revision_id, roles)

        spans: list[tuple[int, int, str]] = []
        cursor = 0
        for row in rows:
            length = len(row["content"])
            spans.append((cursor, cursor + length, row["id"]))
            cursor += length
        return spans

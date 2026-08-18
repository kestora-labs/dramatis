"""Speaking to SQLite or to Postgres from one set of queries.

A project is one SQLite file, and that stays the default and the point: copy the file and you
have copied the project. Postgres exists for the deployment SQLite is wrong for — several
people reading one corpus, a container with no persistent disk, an institution that backs up
databases and not directories.

**The `Store` interface does not change, and neither do its queries.** Everything above this
module goes on writing one dialect, and a thin adapter rewrites what differs on the way to the
driver. The alternative — a second Store, or a query builder — would mean every future method
written twice or written in something other than SQL, and the two would drift.

Three things actually differ, and they are the whole of this module:

**Placeholders.** SQLite writes `?`, Postgres writes `%s`. Rewritten in one place rather than
by every caller, because a caller that forgot would fail only on the dialect nobody tested.

**Stable tie-breaking.** **3.2** and **3.4** both fixed real bugs by ordering on
`created_at, rowid`: a snapshot identifier is a content hash, so ordering by it puts two
snapshots written in the same second into an order decided by hashing, and a diff run
backwards reports every strengthening as a weakening. `rowid` is SQLite's own. Postgres has no
equivalent, so its schema carries an explicit `seq` column instead, and queries write
`{tiebreak}` for whichever the dialect uses. The schemas differ by that one column, and
deliberately: a store is not moved between backends, it is chosen once.

**Pragmas.** SQLite must be told to enforce foreign keys; Postgres always does.

The SQL itself needed nothing else. `ON CONFLICT ... DO UPDATE` is in both, and the column
types the schema uses mean the same thing in each.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

POSTGRES_SCHEMES = ("postgresql://", "postgres://")


def is_postgres(target: Path | str) -> bool:
    """Whether a store target names a Postgres database rather than a file.

    The URL is the choice, as the bullet asks: nothing else in the application has to know
    which backend it is talking to, and a user names one by naming a path or a URL.
    """
    return isinstance(target, str) and target.startswith(POSTGRES_SCHEMES)


class Driver(Protocol):
    """What the store needs of a database, and nothing more."""

    name: str
    placeholder: str
    tiebreak: str
    bookkeeping: tuple[str, ...]

    def connect(self, target: Path | str) -> Any: ...
    def prepare(self, raw: Any) -> None: ...
    def ddl(self, sql: str) -> str: ...


class SQLiteDriver:
    """The default. One file, no server, the thing you can email to a collaborator."""

    name = "sqlite"
    placeholder = "?"
    tiebreak = "rowid"
    bookkeeping = ()  # `rowid` is implicit and never selected by `SELECT *`.

    def connect(self, target: Path | str) -> Any:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def prepare(self, raw: Any) -> None:
        # SQLite enforces foreign keys only when asked, and the schema declares several.
        raw.execute("PRAGMA foreign_keys = ON")

    def ddl(self, sql: str) -> str:
        # `rowid` is implicit, so nothing to add.
        return sql


class PostgresDriver:
    """The alternative, for a deployment a single file is wrong for."""

    name = "postgres"
    placeholder = "%s"
    tiebreak = "seq"
    bookkeeping = ("seq",)
    """Columns that exist for the driver and are not part of the domain.

    Stripped from every row on the way out, so `SELECT *` keeps feeding `Document(**row)` and
    `TextRevision(**row)` as it always has. Without this the ordering column would arrive as
    an unexpected keyword argument — which is precisely what a real Postgres reported the
    first time one was pointed at this code."""

    #: Tables whose rows are ordered with a tie-break, and so need the monotonic column
    #: SQLite gets for free. See the module docstring: the ordering is load-bearing.
    ORDERED = ("text_revisions", "analysis_runs", "snapshots")

    def connect(self, target: Path | str) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:  # pragma: no cover - exercised by the message test
            raise StoreDriverError(
                "the psycopg package is not installed, so a Postgres store cannot be "
                "opened. Install it with `pip install 'dramatis[postgres]'`, or point at a "
                "SQLite file instead."
            ) from error

        # autocommit off: `Store.transaction` decides when work is committed, and the store's
        # immutability rules depend on a failed write leaving nothing behind.
        return psycopg.connect(str(target), row_factory=dict_row)

    def prepare(self, raw: Any) -> None:
        # Postgres enforces foreign keys without being asked.
        return None

    def ddl(self, sql: str) -> str:
        """Add the monotonic column SQLite provides as `rowid`.

        Appended to the three ordered tables rather than all of them, because a column that
        exists only to break ties is noise on a table nothing orders.
        """
        for table in self.ORDERED:
            sql = re.sub(
                rf"(CREATE TABLE IF NOT EXISTS {table} \()",
                r"\1\n    seq        BIGSERIAL,",
                sql,
            )
        return sql


class StoreDriverError(Exception):
    """A store could not be opened. The message names what to install or fix."""


def driver_for(target: Path | str) -> Driver:
    return PostgresDriver() if is_postgres(target) else SQLiteDriver()


class Connection:
    """A database connection that speaks the store's one dialect.

    Wrapping rather than translating at each call site is what keeps `Store` unchanged: every
    `store.connection.execute("... WHERE id = ?", (x,))` already written goes on working, and
    every one written later does too without its author knowing there are two backends.
    """

    def __init__(self, raw: Any, driver: Driver) -> None:
        self._raw = raw
        self._driver = driver

    def translate(self, sql: str) -> str:
        """Rewrite one dialect into the driver's.

        `?` inside a quoted string would be rewritten too, which would be a bug — but the
        store writes no such literal, and a placeholder is the only `?` its SQL contains.
        """
        sql = sql.replace("{tiebreak}", self._driver.tiebreak)
        if self._driver.placeholder != "?":
            sql = sql.replace("?", self._driver.placeholder)
        return sql

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> Any:
        return _Rows(self._raw.execute(self.translate(sql), tuple(parameters)), self._driver)

    def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> Any:
        rows = [tuple(row) for row in parameters]
        if self._driver.name == "postgres":
            # psycopg's executemany returns None rather than a cursor, and the store reads
            # nothing from it; a cursor is returned here only so the two behave alike.
            cursor = self._raw.cursor()
            cursor.executemany(self.translate(sql), rows)
            return cursor
        return self._raw.executemany(self.translate(sql), rows)

    def executescript(self, sql: str) -> None:
        """Run the schema. SQLite has a verb for it; Postgres takes the whole string."""
        prepared = self._driver.ddl(sql)
        if self._driver.name == "postgres":
            self._raw.execute(prepared)
        else:
            self._raw.executescript(prepared)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


class _Rows:
    """A cursor whose rows carry no driver bookkeeping.

    SQLite rows pass through untouched — `sqlite3.Row` supports positional access that some
    callers use, and turning them into dicts would break it for no gain, since `rowid` is
    never selected. Postgres rows are already mappings, and lose the ordering column here so
    that nothing above the driver has to know it exists.
    """

    def __init__(self, cursor: Any, driver: Driver) -> None:
        self._cursor = cursor
        self._strip = getattr(driver, "bookkeeping", ())

    def _clean(self, row: Any) -> Any:
        if row is None or not self._strip:
            return row
        return {key: value for key, value in row.items() if key not in self._strip}

    def fetchone(self) -> Any:
        return self._clean(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [self._clean(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        return (self._clean(row) for row in self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

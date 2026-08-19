"""The same store, against a real Postgres.

**Never a mock.** The bullet asks for it and the reason is concrete: mocking a database
proves the code calls the methods it calls. What broke when a real Postgres was first pointed
at this store was `SELECT *` returning the driver's own ordering column and arriving at
`TextRevision(**row)` as an unexpected keyword — something no mock would have said.

These are part of the ordinary suite rather than behind a deselected marker, and skip only
when no server answers. A test that can never run is the failure **4.6** recorded: it reports
green forever on exactly the machines that could have exercised it.

Start one with:

    docker run -d --name dramatis-pg -e POSTGRES_PASSWORD=dramatis \\
        -e POSTGRES_USER=dramatis -e POSTGRES_DB=dramatis \\
        -p 127.0.0.1:55432:5432 postgres:16
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dramatis.drivers import PostgresDriver, SQLiteDriver, driver_for, is_postgres
from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Document, Store, TextRevision

URL = os.environ.get(
    "DRAMATIS_TEST_POSTGRES", "postgresql://dramatis:dramatis@127.0.0.1:55432/dramatis"
)


def _reachable() -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    try:
        psycopg.connect(URL, connect_timeout=2).close()
    except Exception:
        return False
    return True


REACHABLE = _reachable()
needs_postgres = pytest.mark.skipif(
    not REACHABLE, reason=f"no Postgres answering at {URL}; see this module's docstring"
)


@pytest.fixture
def store():
    """An empty Postgres store. The schema is dropped first so tests do not see each other."""
    import psycopg

    with psycopg.connect(URL) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.commit()
    with Store(URL) as opened:
        yield opened


class TestChoosingTheBackend:
    """No server needed: the choice is made from the target alone."""

    def test_a_url_names_postgres(self) -> None:
        assert is_postgres("postgresql://user@host/db")
        assert is_postgres("postgres://user@host/db")

    def test_a_path_names_sqlite(self, tmp_path: Path) -> None:
        assert not is_postgres(tmp_path / "project.sqlite")
        assert not is_postgres(str(tmp_path / "project.sqlite"))

    def test_the_driver_follows_the_target(self, tmp_path: Path) -> None:
        assert isinstance(driver_for("postgresql://x/y"), PostgresDriver)
        assert isinstance(driver_for(tmp_path / "p.sqlite"), SQLiteDriver)

    def test_a_sqlite_store_still_reports_itself_as_one(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            assert store.backend == "sqlite"

    def test_the_path_reads_back_as_what_was_given(self, tmp_path: Path) -> None:
        # A URL must not be mangled into a Path; every message shows this back to the user.
        assert Store("postgresql://user@host/db").path == "postgresql://user@host/db"
        assert Store(tmp_path / "p.sqlite").path == tmp_path / "p.sqlite"


class TestTheDialectRewriting:
    """The translation, checked without a database."""

    def _connection(self, driver):
        from dramatis.drivers import Connection

        return Connection(raw=None, driver=driver)

    def test_postgres_gets_its_own_placeholder(self) -> None:
        translated = self._connection(PostgresDriver()).translate("SELECT * FROM x WHERE id = ?")
        assert translated == "SELECT * FROM x WHERE id = %s"

    def test_sqlite_is_left_alone(self) -> None:
        sql = "SELECT * FROM x WHERE id = ?"
        assert self._connection(SQLiteDriver()).translate(sql) == sql

    def test_the_tiebreak_becomes_each_dialect_s_own(self) -> None:
        sql = "SELECT id FROM snapshots ORDER BY created_at, {tiebreak}"
        assert self._connection(SQLiteDriver()).translate(sql).endswith("created_at, rowid")
        assert self._connection(PostgresDriver()).translate(sql).endswith("created_at, seq")

    def test_the_ordered_tables_gain_a_monotonic_column(self) -> None:
        from dramatis.store import DDL

        prepared = PostgresDriver().ddl(DDL)
        for table in PostgresDriver.ORDERED:
            block = prepared.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1]
            assert "seq        BIGSERIAL" in block.split(");")[0]

    def test_a_table_nothing_orders_gains_nothing(self) -> None:
        from dramatis.store import DDL

        prepared = PostgresDriver().ddl(DDL)
        documents = prepared.split("CREATE TABLE IF NOT EXISTS documents (")[1].split(");")[0]
        assert "BIGSERIAL" not in documents


@needs_postgres
class TestAgainstARealPostgres:
    def test_the_schema_is_created_and_versioned(self, store: Store) -> None:
        from dramatis.store import STORE_VERSION

        assert store.backend == "postgres"
        assert store.store_version == STORE_VERSION

    def test_writing_and_reading_a_work(self, store: Store) -> None:
        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])

        assert [(w["id"], w["title"]) for w in store.list_works()] == [("work:a", "A Work")]

    def test_a_document_round_trips_through_select_star(self, store: Store) -> None:
        """The bug a real Postgres found: `SELECT *` returns the driver's ordering column,
        and `Document(**row)` refuses an unexpected keyword. It is stripped at the driver."""
        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        store.upsert_document(
            Document(
                id="doc:1",
                work_id="work:a",
                role="narrative",
                sha256="abc",
                content="Ada met Bram.",
                path="one.txt",
            )
        )

        assert store.get_document("doc:1").content == "Ada met Bram."

    def test_a_revision_round_trips_too(self, store: Store) -> None:
        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        store.upsert_text_revision(
            TextRevision(
                id="rev:1",
                work_id="work:a",
                label=None,
                sha256="def",
                created_at="2026-01-01T00:00:00Z",
                document_ids=(),
            )
        )

        assert store.get_text_revision("rev:1").sha256 == "def"

    def test_settings_merge_and_read_back(self, store: Store) -> None:
        store.set_setting("collectives_are_actors", True)
        store.set_setting("collectives_are_actors", False)

        assert store.settings() == {"collectives_are_actors": False}

    def test_ties_break_on_insertion_order_not_on_the_hash(self, store: Store) -> None:
        """What `seq` exists for. 3.2 and 3.4 each fixed a real bug here: a revision
        identifier is a content hash, so ordering by it is ordering by hashing, and a diff
        run backwards reports every strengthening as a weakening."""
        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        same = "2026-02-02T00:00:00Z"
        for identifier in ("rev:zzz", "rev:aaa", "rev:mmm"):
            store.upsert_text_revision(
                TextRevision(
                    id=identifier,
                    work_id="work:a",
                    label=None,
                    sha256=identifier,
                    created_at=same,
                    document_ids=(),
                )
            )

        order = [revision.id for revision in store.list_text_revisions("work:a")]

        assert order == ["rev:zzz", "rev:aaa", "rev:mmm"]
        assert order != sorted(order), "ordering fell back to the identifier, which is a hash"

    def test_the_whole_pipeline_runs_against_it(self, store: Store, tmp_path: Path) -> None:
        """The proof that matters: ingest, extraction, resolution, aggregation and the
        snapshot, with nothing above the driver knowing which database it is."""
        source = tmp_path / "work.txt"
        source.write_text(
            "Ada met Bram at the gate.\n\nBram did not answer her.\n",
            encoding="utf-8",
            newline="",
        )
        reading = json.dumps(
            {
                "characters": [
                    {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram")
                ],
                "interactions": [
                    {
                        "participants": ["Ada", "Bram"],
                        "quotation": "Ada met Bram at the gate.",
                        "note": "",
                    }
                ],
            }
        )
        grouping = json.dumps(
            {
                "groups": [
                    {
                        "canonical_name": n,
                        "forms": [n],
                        "kind": "person",
                        "same_as_registered": "",
                    }
                    for n in ("Ada", "Bram")
                ]
            }
        )

        ingested = ingest_file(store, source, work_title="A Work", collection_name="A Set")
        result = analyse(store, ingested.revision_id, ScriptedProvider([reading, grouping]))
        document = result.snapshot.document

        assert [c["name"] for c in document["characters"]] == ["Ada", "Bram"]
        assert len(document["relations"]) == 1
        assert document["relations"][0]["evidence"][0]["selector"]["exact"]

    def test_the_stored_snapshot_still_validates(self, store: Store, tmp_path: Path) -> None:
        from dramatis.validation import validate_document

        source = tmp_path / "work.txt"
        source.write_text("Ada met Bram at the gate.\n", encoding="utf-8", newline="")
        reading = json.dumps(
            {
                "characters": [
                    {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram")
                ],
                "interactions": [
                    {
                        "participants": ["Ada", "Bram"],
                        "quotation": "Ada met Bram at the gate.",
                        "note": "",
                    }
                ],
            }
        )
        grouping = json.dumps(
            {
                "groups": [
                    {
                        "canonical_name": n,
                        "forms": [n],
                        "kind": "person",
                        "same_as_registered": "",
                    }
                    for n in ("Ada", "Bram")
                ]
            }
        )

        ingested = ingest_file(store, source, work_title="A Work", collection_name="A Set")
        result = analyse(store, ingested.revision_id, ScriptedProvider([reading, grouping]))

        assert validate_document(result.snapshot.document) == []

    def test_a_snapshot_cannot_be_rewritten(self, store: Store, tmp_path: Path) -> None:
        # Invariant 4 is enforced by the store, and must hold on both backends.
        from dramatis.store import ImmutableSnapshotError, StoredSnapshot

        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        store.upsert_text_revision(
            TextRevision(
                id="rev:1",
                work_id="work:a",
                label=None,
                sha256="d",
                created_at="2026-01-01T00:00:00Z",
                document_ids=(),
            )
        )
        store.upsert_analysis_run(
            {"id": "run:1", "model": "m", "prompt_version": "p", "parameters": {}}
        )
        snapshot = StoredSnapshot(
            id="snap:1",
            work_id="work:a",
            text_revision_id="rev:1",
            analysis_run_id="run:1",
            label=None,
            schema_version="0.1.0",
            sha256="one",
            created_at="2026-01-01T00:00:00Z",
            document={"a": 1},
        )
        store.insert_snapshot(snapshot)

        with pytest.raises(ImmutableSnapshotError):
            store.insert_snapshot(
                StoredSnapshot(**{**snapshot.__dict__, "sha256": "two", "document": {"a": 2}})
            )

    def test_a_structure_map_round_trips(self, store: Store) -> None:
        store.save_structure_map("/corpus", {"one.md": {"role": {"value": "narrative"}}}, "now")

        assert store.structure_map("/corpus")["one.md"]["role"]["value"] == "narrative"

    def test_forgetting_a_structure_map_reports_how_many_went(self, store: Store) -> None:
        store.save_structure_map("/corpus", {"one.md": {}, "two.md": {}}, "now")

        assert store.forget_structure_map("/corpus") == 2

    def test_review_decisions_keep_their_order_on_a_tied_timestamp(self, store: Store) -> None:
        """`reviews` joins the ordered tables (5.1), so it needs `seq` for the same reason
        the others do: the newest decision is the one that stands, and two taken in the same
        second must not be reordered by anything else."""
        from dramatis.store import ReviewDecision, StoredSnapshot

        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        store.upsert_text_revision(
            TextRevision(
                id="rev:1",
                work_id="work:a",
                label=None,
                sha256="d",
                created_at="2026-01-01T00:00:00Z",
                document_ids=(),
            )
        )
        store.upsert_analysis_run(
            {"id": "run:1", "model": "m", "prompt_version": "p", "parameters": {}}
        )
        store.insert_snapshot(
            StoredSnapshot(
                id="snap:a",
                work_id="work:a",
                text_revision_id="rev:1",
                analysis_run_id="run:1",
                label=None,
                schema_version="0.1.0",
                sha256="one",
                created_at="2026-01-01T00:00:00Z",
                document={"a": 1},
            )
        )
        same = "2026-03-03T00:00:00Z"
        for status in ("accepted", "rejected", "accepted"):
            store.append_review(
                ReviewDecision(
                    work_id="work:a",
                    subject_kind="character",
                    subject_id="char:a",
                    status=status,
                    snapshot_id="snap:a",
                    decided_at=same,
                )
            )

        recorded = store.list_reviews("work:a")

        assert [decision.status for decision in recorded] == ["accepted", "rejected", "accepted"]
        assert store.current_reviews("work:a")[("character", "char:a")].status == "accepted"

    def _a_snapshot(self, store: Store, identifier: str = "snap:a") -> None:
        """The rows a correction's foreign keys need. Postgres enforces them; SQLite has to
        be asked to, and both do here."""
        from dramatis.store import StoredSnapshot

        store.upsert_collection("col:a", "A Set")
        store.upsert_work("work:a", "col:a", "A Work", segment_types=[])
        store.upsert_text_revision(
            TextRevision(
                id="rev:1",
                work_id="work:a",
                label=None,
                sha256="d",
                created_at="2026-01-01T00:00:00Z",
                document_ids=(),
            )
        )
        store.upsert_analysis_run(
            {"id": "run:1", "model": "m", "prompt_version": "p", "parameters": {}}
        )
        store.insert_snapshot(
            StoredSnapshot(
                id=identifier,
                work_id="work:a",
                text_revision_id="rev:1",
                analysis_run_id="run:1",
                label=None,
                schema_version="0.1.0",
                sha256="one",
                created_at="2026-01-01T00:00:00Z",
                document={"a": 1},
            )
        )

    def test_corrections_keep_their_order_on_a_tied_timestamp(self, store: Store) -> None:
        """`corrections` joins the ordered tables (5.2). The newest correction is the one that
        is applied, so two made in the same second must not be reordered by anything else."""
        from dramatis.store import Correction

        self._a_snapshot(store)
        same = "2026-03-03T00:00:00Z"
        for name in ("Ada Mbeki", "Ada M. Mbeki", "A. M. Mbeki"):
            store.append_correction(
                Correction(
                    work_id="work:a",
                    subject_kind="character",
                    subject_id="char:a",
                    field="name",
                    value=name,
                    was="Ada",
                    snapshot_id="snap:a",
                    corrected_at=same,
                )
            )

        recorded = store.list_corrections("work:a")

        assert [entry.value for entry in recorded] == ["Ada Mbeki", "Ada M. Mbeki", "A. M. Mbeki"]
        standing = store.current_corrections("work:a")
        assert standing[("character", "char:a", "name")].value == "A. M. Mbeki"

    def test_a_correction_reads_back_as_the_type_it_was_written_as(self, store: Store) -> None:
        """JSON on the way down and back, on both backends. A list stored as text and read
        back as text would reach the schema as a string and be rejected there."""
        from dramatis.store import Correction

        self._a_snapshot(store)
        store.append_correction(
            Correction(
                work_id="work:a",
                subject_kind="relation",
                subject_id="rel:a--b",
                field="types",
                value=["kinship", "estrangement"],
                was=None,
                snapshot_id="snap:a",
                corrected_at="2026-03-03T00:00:00Z",
            )
        )

        only = store.list_corrections("work:a")[0]
        assert only.value == ["kinship", "estrangement"]
        assert only.was is None

    def test_conflicts_round_trip(self, store: Store) -> None:
        from dramatis.store import CorrectionConflict

        self._a_snapshot(store)
        store.append_correction_conflicts(
            [
                CorrectionConflict(
                    work_id="work:a",
                    subject_kind="character",
                    subject_id="char:a",
                    field="kind",
                    proposed="collective",
                    held="entity",
                    snapshot_id="snap:a",
                    noticed_at="2026-03-03T00:00:00Z",
                )
            ]
        )

        found = store.list_correction_conflicts("work:a", snapshot_id="snap:a")
        assert [(c.field, c.proposed, c.held) for c in found] == [("kind", "collective", "entity")]

    def test_a_merge_moves_claims_and_retires_in_one_transaction(self, store: Store) -> None:
        """`rewrite_characters` is the one write that hands a surface form from one character
        to another (5.3). Both backends have to land it whole: a half-applied move leaves a
        name denoting nobody, and the alias primary key would refuse it half-way."""
        from dramatis import identity
        from dramatis.store import RegisteredCharacter

        store.upsert_collection("col:a", "A Set")
        store.upsert_character(
            RegisteredCharacter(id="char:ada", collection_id="col:a", name="Ada", kind="person")
        )
        store.upsert_character(
            RegisteredCharacter(
                id="char:miss-ada", collection_id="col:a", name="Miss Ada", kind="person"
            )
        )

        identity.merge(store, "col:a", into="char:ada", absorb="char:miss-ada")

        survivor = store.find_character_by_form("col:a", "Miss Ada")
        assert survivor is not None and survivor.id == "char:ada"
        assert [c.id for c in store.list_characters("col:a")] == ["char:ada"]
        retired = store.get_character("char:miss-ada")
        assert retired is not None and retired.merged_into == "char:ada"
        assert store.merged_into("col:a") == {"char:miss-ada": "char:ada"}

    def test_registry_decisions_keep_their_order_on_a_tied_timestamp(self, store: Store) -> None:
        from dramatis.store import RegistryDecision

        store.upsert_collection("col:a", "A Set")
        same = "2026-04-04T00:00:00Z"
        for action in ("merge", "split", "merge"):
            store.append_registry_decision(
                RegistryDecision(
                    collection_id="col:a",
                    action=action,
                    source_id="char:a",
                    target_id="char:b",
                    forms=("A",),
                    decided_at=same,
                )
            )

        assert [d.action for d in store.list_registry_decisions("col:a")] == [
            "merge",
            "split",
            "merge",
        ]

    def test_a_column_added_later_is_added_to_an_existing_database(self, store: Store) -> None:
        """The bug a real project file found: `CREATE TABLE IF NOT EXISTS` adds tables and
        never columns, so a store made before 5.3 has no `merged_into` and every read of the
        registry fails on it. The migration has to work on both backends, and the dialects
        differ in how a table's columns are asked for — which is why it asks the cursor."""
        import psycopg

        from dramatis.store import Store as OpenStore

        store.upsert_collection("col:a", "A Set")
        store.close()

        with psycopg.connect(URL) as connection:
            connection.execute("ALTER TABLE characters DROP COLUMN merged_into")
            connection.commit()

        with OpenStore(URL) as reopened:
            assert "merged_into" in reopened.connection.columns("characters")
            assert reopened.merged_into("col:a") == {}

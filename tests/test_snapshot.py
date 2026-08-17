"""Tests for building, validating and storing snapshots.

The two properties that matter: a snapshot that does not satisfy the published schema is
never written, and one that exists never changes. Both are about what a citation can rely
on — an identifier that names a different graph tomorrow is worse than no identifier.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.aggregation import Aggregation, aggregate
from dramatis.extraction import (
    PROMPT_VERSION,
    Extraction,
    MentionedCharacter,
    ObservedInteraction,
    Window,
    WindowFinding,
)
from dramatis.ingest import ingest_file
from dramatis.pipeline import PIPELINE_VERSION, PipelineError, analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.resolution import resolve
from dramatis.schema import DOCUMENT_VERSION, load_schema, schema_version
from dramatis.segmentation import segment_text
from dramatis.snapshot import (
    AnalysisRun,
    SnapshotError,
    build_document,
    canonical_json,
    document_hash,
    save_snapshot,
    snapshot_id,
)
from dramatis.store import ImmutableSnapshotError, Store
from dramatis.validation import validate_document

PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"


def a_text(tmp_path: Path, body: str = PASSAGE, name: str = "work.txt") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


@pytest.fixture
def project(tmp_path: Path):
    with Store(tmp_path / "project.sqlite") as store:
        result = ingest_file(
            store, a_text(tmp_path), work_title="A Work", collection_name="A Collection"
        )
        yield store, result


def a_reply(characters: list[dict], interactions: list[dict]) -> str:
    return json.dumps({"characters": characters, "interactions": interactions})


def one_window_reply() -> str:
    return a_reply(
        [
            {"name": "Ada", "aliases": [], "kind": "person"},
            {"name": "Bram", "aliases": [], "kind": "person"},
            {"name": "Cai", "aliases": [], "kind": "person"},
        ],
        [
            {
                "participants": ["Ada", "Bram"],
                "quotation": "Ada met Bram at the gate.",
                "note": "They meet.",
            },
            {
                "participants": ["Ada", "Cai"],
                "quotation": "Cai spoke to Ada alone.",
                "note": "",
            },
        ],
    )


def a_grouping() -> str:
    return json.dumps(
        {
            "groups": [
                {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                for n in ("Ada", "Bram", "Cai")
            ]
        }
    )


# -- run metadata -----------------------------------------------------------------------------


class TestAnalysisRun:
    def test_the_identifier_covers_what_determines_the_analysis(self) -> None:
        base = AnalysisRun(model="m", prompt_version="p", started_at="t")

        assert base.id == AnalysisRun(model="m", prompt_version="p", started_at="t").id
        assert base.id != AnalysisRun(model="m2", prompt_version="p", started_at="t").id
        assert base.id != AnalysisRun(model="m", prompt_version="p2", started_at="t").id
        assert base.id != AnalysisRun(model="m", prompt_version="p", started_at="t2").id

    def test_two_executions_of_one_configuration_are_two_runs(self) -> None:
        """Models are not deterministic; collapsing them would make a snapshot ambiguous."""
        first = AnalysisRun(model="m", prompt_version="p", started_at="09:00")
        second = AnalysisRun(model="m", prompt_version="p", started_at="10:00")

        assert first.id != second.id

    def test_it_records_what_a_citation_needs(self) -> None:
        run = AnalysisRun(
            model="claude-opus-5",
            provider="anthropic",
            prompt_version="extract-v1",
            pipeline_version=PIPELINE_VERSION,
            application_version="0.1.0.dev0",
            parameters={"effort": "medium"},
            started_at="t",
        )
        rendered = run.as_schema()

        assert rendered["model"] == "claude-opus-5"
        assert rendered["prompt_version"] == "extract-v1"
        assert rendered["application_version"] == "0.1.0.dev0"
        assert rendered["parameters"] == {"effort": "medium"}


class TestIdentifiers:
    def test_a_snapshot_is_identified_by_both_of_its_axes(self) -> None:
        assert snapshot_id("rev:a", "run:x") != snapshot_id("rev:b", "run:x")
        assert snapshot_id("rev:a", "run:x") != snapshot_id("rev:a", "run:y")
        assert snapshot_id("rev:a", "run:x") == snapshot_id("rev:a", "run:x")

    def test_canonical_json_is_order_independent(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
        assert document_hash({"b": 1, "a": 2}) == document_hash({"a": 2, "b": 1})


class TestDocumentVersion:
    def test_it_agrees_with_the_schema_it_is_written_against(self) -> None:
        """A document claiming a version the schema does not recognise is worse than none."""
        assert DOCUMENT_VERSION.startswith(schema_version() + ".")
        assert load_schema()["properties"]["schema_version"]["pattern"]


# -- building ---------------------------------------------------------------------------------


class TestBuildingADocument:
    def test_the_document_validates(self, project) -> None:
        store, ingested = project
        segmentation = segment_text(PASSAGE)
        extraction = Extraction(
            findings=(
                WindowFinding(
                    window=Window(0, 0, len(PASSAGE), (0, 1, 2)),
                    characters=(MentionedCharacter("Ada"), MentionedCharacter("Bram")),
                    interactions=(
                        ObservedInteraction(
                            participants=("Ada", "Bram"),
                            quotation="Ada met Bram at the gate.",
                            segment_position=0,
                        ),
                    ),
                ),
            ),
            prompt_version="extract-v1",
            model="m",
            provider="scripted",
        )
        resolution = resolve(extraction, store, ingested.collection_id)
        aggregation = aggregate(extraction.interactions, resolution, segmentation)

        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=AnalysisRun(model="m", prompt_version="extract-v1", started_at="t"),
            character_ids=set(resolution.assignments.values()),
            aggregation=aggregation,
        )

        assert validate_document(document) == []

    def test_both_axes_are_present_and_distinct(self, project) -> None:
        store, ingested = project
        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=AnalysisRun(model="m", prompt_version="p", started_at="t"),
            character_ids=set(),
            aggregation=Aggregation(),
        )
        snapshot = document["snapshot"]

        assert snapshot["text_revision_id"] == ingested.revision_id
        assert snapshot["analysis_run_id"].startswith("run:")
        assert snapshot["text_revision_id"] != snapshot["analysis_run_id"]

    def test_an_unknown_work_is_a_clean_error(self, project) -> None:
        store, ingested = project
        with pytest.raises(SnapshotError, match="unknown work"):
            build_document(
                store,
                work_id="work:nope",
                text_revision_id=ingested.revision_id,
                run=AnalysisRun(model="m", prompt_version="p"),
                character_ids=set(),
                aggregation=Aggregation(),
            )

    def test_an_unknown_revision_is_a_clean_error(self, project) -> None:
        store, ingested = project
        with pytest.raises(SnapshotError, match="unknown text revision"):
            build_document(
                store,
                work_id=ingested.work_id,
                text_revision_id="rev:nope",
                run=AnalysisRun(model="m", prompt_version="p"),
                character_ids=set(),
                aggregation=Aggregation(),
            )


# -- storing ------------------------------------------------------------------------------------


class TestStoring:
    def test_an_invalid_document_is_never_written(self, project) -> None:
        """A broken record under an identifier something may already cite."""
        store, _ = project
        with pytest.raises(SnapshotError, match="does not satisfy the published schema"):
            save_snapshot(store, {"schema_version": "0.1.0"})

        assert store.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0

    def test_a_stored_snapshot_reads_back_identically(self, project) -> None:
        store, ingested = project
        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=AnalysisRun(model="m", prompt_version="p", started_at="t"),
            character_ids=set(),
            aggregation=Aggregation(),
        )
        stored = save_snapshot(store, document)

        read_back = store.get_snapshot(stored.id)
        assert read_back is not None
        assert read_back.document == document
        assert read_back.sha256 == document_hash(document)

    def test_writing_the_same_snapshot_twice_is_harmless(self, project) -> None:
        store, ingested = project
        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=AnalysisRun(model="m", prompt_version="p", started_at="t"),
            character_ids=set(),
            aggregation=Aggregation(),
        )
        save_snapshot(store, document)
        save_snapshot(store, document)

        assert len(store.list_snapshots(ingested.work_id)) == 1

    def test_changing_an_existing_snapshot_is_refused(self, project) -> None:
        """Invariant 4: a snapshot whose meaning changed under a live citation."""
        store, ingested = project
        run = AnalysisRun(model="m", prompt_version="p", started_at="t")
        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=run,
            character_ids=set(),
            aggregation=Aggregation(),
        )
        save_snapshot(store, document)

        tampered = dict(document)
        tampered["characters"] = [{"id": "char:ghost", "name": "Ghost", "provenance": "observed"}]

        with pytest.raises(ImmutableSnapshotError, match="immutable"):
            save_snapshot(store, tampered)

    def test_the_analysis_run_is_recorded_separately(self, project) -> None:
        store, ingested = project
        run = AnalysisRun(
            model="claude-opus-5",
            prompt_version="extract-v1",
            started_at="t",
            parameters={"effort": "medium"},
        )
        document = build_document(
            store,
            work_id=ingested.work_id,
            text_revision_id=ingested.revision_id,
            run=run,
            character_ids=set(),
            aggregation=Aggregation(),
        )
        save_snapshot(store, document)

        recorded = store.get_analysis_run(run.id)
        assert recorded is not None
        assert recorded["model"] == "claude-opus-5"
        assert recorded["parameters"] == {"effort": "medium"}


# -- end to end ------------------------------------------------------------------------------------


class TestTheWholePipeline:
    def test_a_revision_becomes_a_valid_snapshot(self, project) -> None:
        store, ingested = project
        provider = ScriptedProvider([one_window_reply(), a_grouping()])

        result = analyse(store, ingested.revision_id, provider)

        assert validate_document(result.snapshot.document) == []
        assert len(result.aggregation) == 2
        assert result.verification.rejected == 0

    def test_the_snapshot_names_the_model_and_prompt_that_produced_it(self, project) -> None:
        store, ingested = project
        provider = ScriptedProvider([one_window_reply(), a_grouping()])

        result = analyse(store, ingested.revision_id, provider)
        run = result.snapshot.document["analysis_runs"][0]

        assert run["prompt_version"] == PROMPT_VERSION
        assert run["pipeline_version"] == PIPELINE_VERSION
        assert run["application_version"]
        assert run["parameters"]["weight_basis"] == "interaction_passages"

    def test_the_snapshot_records_the_prompt_itself_not_only_its_label(self, project) -> None:
        """D18. A field that never reaches the stored document is a guarantee in name only,
        and this is the field two snapshots are compared on."""
        from dramatis.aggregation import require_comparable_snapshots
        from dramatis.extraction import prompt_sha256

        store, ingested = project
        provider = ScriptedProvider([one_window_reply(), a_grouping()])

        document = analyse(store, ingested.revision_id, provider).snapshot.document

        assert document["analysis_runs"][0]["prompt_sha256"] == prompt_sha256()
        require_comparable_snapshots(document, document)

    def test_the_run_records_the_terms_the_project_is_studied_under(self, project) -> None:
        """D19. A reader of a snapshot must be able to see which question was asked, not
        only which text was read."""
        from dramatis.store import COLLECTIVES_ARE_ACTORS

        store, ingested = project
        store.set_setting(COLLECTIVES_ARE_ACTORS, True)

        document = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        assert document["analysis_runs"][0]["parameters"][COLLECTIVES_ARE_ACTORS] is True

    def test_the_setting_changes_the_prompt_the_run_records(self, project) -> None:
        """The setting is inside the provenance guarantee rather than beside it: it is not
        a note about the run, it is part of what the run asked."""
        from dramatis.store import COLLECTIVES_ARE_ACTORS

        store, ingested = project
        without = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        store.set_setting(COLLECTIVES_ARE_ACTORS, True)
        with_groups = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        assert (
            without["analysis_runs"][0]["prompt_sha256"]
            != with_groups["analysis_runs"][0]["prompt_sha256"]
        )

    def test_changing_the_setting_makes_snapshots_incomparable(self, project) -> None:
        """Changeable afterwards, but not silently: a graph of people and a graph of people
        and groups are not two readings of one corpus."""
        from dramatis.aggregation import ComparabilityError, require_comparable_snapshots
        from dramatis.store import COLLECTIVES_ARE_ACTORS

        store, ingested = project
        before = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        store.set_setting(COLLECTIVES_ARE_ACTORS, True)
        after = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        with pytest.raises(ComparabilityError, match="collectives"):
            require_comparable_snapshots(before, after)

    def test_two_runs_under_one_prompt_stay_comparable(self, project) -> None:
        from dramatis.aggregation import require_comparable_snapshots

        store, ingested = project

        first = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document
        second = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        require_comparable_snapshots(first, second)

    def test_editing_the_prompt_makes_the_next_snapshot_incomparable(
        self, project, monkeypatch
    ) -> None:
        """The acceptance criterion for 1.12, and the reason the hash exists: the version
        label is unchanged throughout, so nothing but the hash could catch this."""
        from dramatis.aggregation import ComparabilityError, require_comparable_snapshots
        from dramatis.extraction import system_prompt

        store, ingested = project
        before = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        edited = system_prompt() + "\nAlso: prefer the shortest quotation available.\n"
        monkeypatch.setattr("dramatis.extraction.system_prompt", lambda **_: edited)

        after = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply(), a_grouping()])
        ).snapshot.document

        assert (
            before["analysis_runs"][0]["prompt_version"]
            == after["analysis_runs"][0]["prompt_version"]
        ), "the labels agree, which is exactly the case the hash is for"

        with pytest.raises(ComparabilityError, match="different extraction prompts"):
            require_comparable_snapshots(before, after)

    def test_an_invented_quotation_never_reaches_the_snapshot(self, project) -> None:
        store, ingested = project
        reply = a_reply(
            [
                {"name": "Ada", "aliases": [], "kind": "person"},
                {"name": "Bram", "aliases": [], "kind": "person"},
            ],
            [
                {
                    "participants": ["Ada", "Bram"],
                    "quotation": "Ada met Bram at the gate.",
                    "note": "",
                },
                {"participants": ["Ada", "Bram"], "quotation": "They embraced warmly.", "note": ""},
            ],
        )
        provider = ScriptedProvider([reply, a_grouping()])

        result = analyse(store, ingested.revision_id, provider)
        quotations = [
            piece["selector"]["exact"]
            for relation in result.snapshot.document["relations"]
            for piece in relation["evidence"]
        ]

        assert "They embraced warmly." not in quotations
        assert result.verification.rejected == 1

    def test_a_character_survives_a_rejected_quotation(self, project) -> None:
        """Losing a quotation should not lose a person."""
        store, ingested = project
        reply = a_reply(
            [
                {"name": "Ada", "aliases": [], "kind": "person"},
                {"name": "Bram", "aliases": [], "kind": "person"},
            ],
            [{"participants": ["Ada", "Bram"], "quotation": "Invented entirely.", "note": ""}],
        )
        provider = ScriptedProvider([reply, a_grouping()])

        result = analyse(store, ingested.revision_id, provider)
        names = {c["name"] for c in result.snapshot.document["characters"]}

        assert {"Ada", "Bram"} <= names
        assert result.snapshot.document["relations"] == []

    def test_the_snapshot_is_listed_against_its_work(self, project) -> None:
        store, ingested = project
        provider = ScriptedProvider([one_window_reply(), a_grouping()])

        result = analyse(store, ingested.revision_id, provider)
        listed = store.list_snapshots(ingested.work_id)

        assert [s.id for s in listed] == [result.snapshot.id]

    def test_two_runs_on_one_revision_are_two_snapshots(self, project) -> None:
        """The second axis: same text, different analysis."""
        store, ingested = project

        first = analyse(
            store,
            ingested.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-08-16T09:00:00+00:00",
        )
        second = analyse(
            store,
            ingested.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-08-16T10:00:00+00:00",
        )

        assert first.snapshot.id != second.snapshot.id
        assert first.snapshot.text_revision_id == second.snapshot.text_revision_id
        assert first.snapshot.analysis_run_id != second.snapshot.analysis_run_id
        assert len(store.list_snapshots(ingested.work_id)) == 2

    def test_identical_inputs_from_identical_state_yield_the_same_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Phase 3 cannot diff snapshots if identical inputs disagree.

        Two separate stores, because "identical inputs" includes the registry: a store
        that already knows the characters is not the same starting state.

        Ingest is given a fixed clock too. Without it this test is flaky rather than
        wrong: a revision records when it was ingested, timestamps have second resolution,
        and two ingests either side of a second boundary produce different documents for
        the same text. That is correct behaviour — the graph is identical, the ingest
        metadata honestly differs — so the test pins the clock instead of the code
        pretending the two ingests happened at once.
        """
        at = "2026-08-16T09:00:00+00:00"
        hashes = []
        for name in ("first", "second"):
            with Store(tmp_path / f"{name}.sqlite") as store:
                ingested = ingest_file(
                    store,
                    a_text(tmp_path),
                    work_title="A Work",
                    collection_name="A Collection",
                    now=at,
                )
                result = analyse(
                    store,
                    ingested.revision_id,
                    ScriptedProvider([one_window_reply(), a_grouping()]),
                    now=at,
                )
                hashes.append((result.snapshot.id, result.snapshot.sha256))

        assert hashes[0] == hashes[1]

    def test_ingest_time_is_part_of_what_a_snapshot_records(self, tmp_path: Path) -> None:
        """The flake above, stated as the property it actually is.

        Two ingests of identical text at different moments produce the same revision — the
        identifier comes from the content hash — but documents that differ in when the
        revision was recorded. Nothing here should paper over that.
        """
        revisions = []
        for name, at in (("a", "2026-08-16T09:00:00+00:00"), ("b", "2026-08-16T10:00:00+00:00")):
            with Store(tmp_path / f"{name}.sqlite") as store:
                ingested = ingest_file(
                    store,
                    a_text(tmp_path),
                    work_title="A Work",
                    collection_name="A Collection",
                    now=at,
                )
                revision = store.get_text_revision(ingested.revision_id)
                revisions.append((ingested.revision_id, revision.created_at))

        assert revisions[0][0] == revisions[1][0], "same text, same revision identifier"
        assert revisions[0][1] != revisions[1][1], "different moment, honestly recorded"

    def test_a_populated_registry_does_less_work_without_becoming_another_analysis(
        self, project
    ) -> None:
        """Both facts are true and they belong in different places.

        The second run genuinely does less: resolution consults the model only for names it
        does not already know, so a re-analysis over a populated registry never calls it,
        and `Resolution.prompt_version` says so. That is an outcome.

        It used to be recorded in the run's parameters, which are the material a run's
        identity is hashed from — so two analyses of one configuration became two
        configurations, for a reason about the state of the registry rather than about the
        analysis. Phase 3 cannot survive that: holding the analysis still across two
        revisions is what makes a diff attributable to the text, and it was not expressible.
        The parameters now record the resolution prompt this run was *configured* to use,
        which it was configured to use whether or not it needed it (D35).
        """
        store, ingested = project
        at = "2026-08-16T09:00:00+00:00"

        first = analyse(
            store,
            ingested.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now=at,
        )
        second = analyse(
            store, ingested.revision_id, ScriptedProvider([one_window_reply()]), now=at
        )

        # The outcome is still observable, and still honest.
        assert first.resolution.prompt_version == "resolve-v1"
        assert second.resolution.prompt_version is None, "no unknown names, so no call"

        # The configuration is not perturbed by it.
        assert first.snapshot.analysis_run_id == second.snapshot.analysis_run_id

        def graph(result) -> tuple:
            document = result.snapshot.document
            return (
                sorted(c["id"] for c in document["characters"]),
                sorted((r["id"], r["weight"]) for r in document["relations"]),
            )

        assert graph(first) == graph(second), "same configuration, same text, same graph"

    def test_an_unknown_revision_is_a_clean_error(self, project) -> None:
        store, _ = project
        with pytest.raises(PipelineError, match="unknown text revision"):
            analyse(store, "rev:nope", ScriptedProvider([]))

    def test_a_wholly_unverifiable_run_is_refused(self, project) -> None:
        store, ingested = project
        reply = a_reply(
            [
                {"name": "Ada", "aliases": [], "kind": "person"},
                {"name": "Bram", "aliases": [], "kind": "person"},
            ],
            [
                {"participants": ["Ada", "Bram"], "quotation": f"Invention {n}.", "note": ""}
                for n in range(6)
            ],
        )
        from dramatis.verification import VerificationError

        with pytest.raises(VerificationError):
            analyse(store, ingested.revision_id, ScriptedProvider([reply, a_grouping()]))

        assert store.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


class TestHoldingOneAxisStill:
    """Phase 3.6, and the sentence phase 3's acceptance rests on.

    *Re-run an analysis against a new text revision while holding the prompt constant, and
    against a new prompt while holding the text constant.* Both halves are only sayable if a
    run records what it was configured to do rather than what happened to it — see D35.
    """

    def _second_revision(self, tmp_path: Path, store: Store):
        edited = a_text(
            tmp_path,
            PASSAGE + "\nCai and Bram spoke at length about the gate.\n",
            name="edited.txt",
        )
        return ingest_file(store, edited, work_title="A Work", collection_name="A Collection")

    def test_the_same_analysis_over_two_revisions_credits_the_text(
        self, project, tmp_path: Path
    ) -> None:
        from dramatis.diff import TEXT, diff_snapshots

        store, first = project
        second = self._second_revision(tmp_path, store)

        # A month apart, so the run identifiers differ; the configuration does not.
        before = analyse(
            store,
            first.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-01-01T00:00:00+00:00",
        )
        after = analyse(
            store,
            second.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-02-01T00:00:00+00:00",
        )

        assert before.snapshot.analysis_run_id != after.snapshot.analysis_run_id
        result = diff_snapshots(before.snapshot.document, after.snapshot.document)

        assert result.attribution == TEXT
        assert not result.warnings

    def test_a_different_setting_over_one_revision_credits_the_analysis(self, project) -> None:
        from dramatis.diff import ANALYSIS, diff_snapshots

        store, first = project
        before = analyse(
            store,
            first.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-01-01T00:00:00+00:00",
            effort="medium",
        )
        after = analyse(
            store,
            first.revision_id,
            ScriptedProvider([one_window_reply(), a_grouping()]),
            now="2026-02-01T00:00:00+00:00",
            effort="low",
        )

        result = diff_snapshots(before.snapshot.document, after.snapshot.document)
        assert result.attribution == ANALYSIS

    def test_the_recorded_resolution_prompt_no_longer_depends_on_the_registry(
        self, project
    ) -> None:
        """The defect this bullet existed to remove.

        The field used to be null whenever resolution answered from the registry without a
        model call, which is every analysis after the first — so a second run of identical
        settings recorded a different configuration for a reason about the registry rather
        than about the analysis.
        """
        store, first = project
        at = "2026-01-01T00:00:00+00:00"

        one = analyse(
            store, first.revision_id, ScriptedProvider([one_window_reply(), a_grouping()]), now=at
        )
        two = analyse(store, first.revision_id, ScriptedProvider([one_window_reply()]), now=at)

        def recorded(result):
            run = next(
                entry
                for entry in result.snapshot.document["analysis_runs"]
                if entry["id"] == result.snapshot.analysis_run_id
            )
            return run["parameters"]["resolution_prompt_version"]

        assert recorded(one) == recorded(two) == "resolve-v1"
        assert two.resolution.prompt_version is None, "the outcome is still observable"

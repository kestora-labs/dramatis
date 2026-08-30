"""Tests for the ``dramatis`` command line interface.

Exit codes are the contract: 0 means every document passed, non-zero means at least one
did not. Anything scripting Dramatis in CI depends on that more than on the wording of the
messages.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dramatis.cli import main
from dramatis.store import COLLECTIVES_ARE_ACTORS, Store
from tests.documents import minimal_document


def _write(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def test_valid_document_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path / "good.json", minimal_document())

    assert main(["validate", str(path)]) == 0
    assert "ok" in capsys.readouterr().out


def test_invalid_document_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    document = minimal_document()
    document["relations"][0]["source"] = "char:missing"
    path = _write(tmp_path / "bad.json", document)

    assert main(["validate", str(path)]) == 1

    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "char:missing" in captured.err
    assert "reference" in captured.err


def test_several_documents_are_all_reported(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    good = _write(tmp_path / "good.json", minimal_document())
    bad = _write(tmp_path / "bad.json", {"schema_version": "0.1.0"})

    assert main(["validate", str(good), str(bad)]) == 1

    captured = capsys.readouterr()
    assert "good.json" in captured.out
    assert "bad.json" in captured.err


def test_quiet_reports_failures_only(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path / "good.json", minimal_document())

    assert main(["validate", "--quiet", str(path)]) == 0
    assert capsys.readouterr().out == ""


def test_json_output_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    document = minimal_document()
    document["relations"][0]["target"] = "char:missing"
    path = _write(tmp_path / "bad.json", document)

    assert main(["validate", "--json", str(path)]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "0.1"
    assert payload["results"][0]["valid"] is False
    assert payload["results"][0]["issues"][0]["kind"] == "reference"
    assert payload["results"][0]["issues"][0]["path"] == "/relations/0/target"


def test_version_flag_reports_the_application_version(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert "dramatis" in capsys.readouterr().out


def test_no_subcommand_is_an_error(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code != 0


# -- ingest ---------------------------------------------------------------------------


def _text_file(tmp_path: Path, name: str = "pride.txt", body: str = "Ada met Bram.\n") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


def _raise(error: type[BaseException]):
    """An `input` that answers the way a closed or interrupted stdin does."""

    def refuse(*_args, **_kwargs):
        raise error()

    return refuse


def test_ingest_reports_what_it_stored(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store = tmp_path / "project.sqlite"

    assert main(["ingest", str(_text_file(tmp_path)), "--store", str(store)]) == 0

    out = capsys.readouterr().out
    assert "ingested" in out
    assert "rev:" in out
    assert store.is_file()


def test_ingest_is_idempotent_and_says_so(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    store = tmp_path / "project.sqlite"
    path = _text_file(tmp_path)

    assert main(["ingest", str(path), "--store", str(store)]) == 0
    capsys.readouterr()
    assert main(["ingest", str(path), "--store", str(store)]) == 0

    assert "already present" in capsys.readouterr().out


def test_ingest_json_output_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    store = tmp_path / "project.sqlite"

    assert (
        main(
            [
                "ingest",
                str(_text_file(tmp_path)),
                "--store",
                str(store),
                "--work",
                "Pride and Prejudice",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["work_id"] == "work:pride-and-prejudice"
    assert payload["revision_id"].startswith("rev:")
    assert len(payload["sha256"]) == 64
    assert payload["already_present"] is False


def test_ingest_missing_file_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert (
        main(["ingest", str(tmp_path / "absent.txt"), "--store", str(tmp_path / "p.sqlite")]) == 1
    )
    assert "no such file" in capsys.readouterr().err


def test_ingest_rejects_an_unknown_role(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["ingest", str(_text_file(tmp_path)), "--role", "appendix"])

    assert exit_info.value.code != 0


# -- analyse --------------------------------------------------------------------------


def test_analyse_reports_an_unknown_revision_without_calling_a_provider(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """It fails on the revision before reaching the network, so this needs no credential."""
    store = tmp_path / "project.sqlite"
    main(["ingest", str(_text_file(tmp_path)), "--store", str(store)])
    capsys.readouterr()

    assert main(["analyse", "rev:nope", "--store", str(store)]) == 1
    assert "unknown text revision" in capsys.readouterr().err


def test_analyse_accepts_its_options(tmp_path: Path) -> None:
    from dramatis.cli import _build_parser

    parsed = _build_parser().parse_args(
        [
            "analyse",
            "rev:abc",
            "--store",
            str(tmp_path / "p.sqlite"),
            "--effort",
            "high",
            "--label",
            "First pass",
            "--json",
        ]
    )

    assert (parsed.revision, parsed.effort, parsed.label, parsed.as_json) == (
        "rev:abc",
        "high",
        "First pass",
        True,
    )


class TestAskingWhetherCollectivesAreActors:
    """D19. Asked on the ingest that creates a project, because D16 leaves no separate
    initialisation step to hang the question on."""

    @staticmethod
    def _project(tmp_path: Path) -> Path:
        return tmp_path / "project.sqlite"

    def test_the_flag_records_the_answer(self, tmp_path: Path) -> None:
        store = self._project(tmp_path)
        assert (
            main(
                [
                    "ingest",
                    str(_text_file(tmp_path)),
                    "--store",
                    str(store),
                    "--collectives-as-actors",
                ]
            )
            == 0
        )

        with Store(store) as opened:
            assert opened.get_setting(COLLECTIVES_ARE_ACTORS) is True

    def test_the_negation_records_the_answer_too(self, tmp_path: Path) -> None:
        """Choosing the default deliberately is not the same as never being asked, and a
        project that recorded its answer is not asked again."""
        store = self._project(tmp_path)
        main(
            [
                "ingest",
                str(_text_file(tmp_path)),
                "--store",
                str(store),
                "--no-collectives-as-actors",
            ]
        )

        with Store(store) as opened:
            assert opened.get_setting(COLLECTIVES_ARE_ACTORS) is False

    def test_the_two_flags_cannot_both_be_given(self, tmp_path: Path) -> None:
        from dramatis.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(
                ["ingest", "x.txt", "--collectives-as-actors", "--no-collectives-as-actors"]
            )

    def test_a_non_interactive_run_takes_the_default_and_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A pipeline that blocks waiting for input nobody will type is worse than one that
        states its assumption. pytest gives a non-tty stdin, which is the case under test."""
        store = self._project(tmp_path)

        assert main(["ingest", str(_text_file(tmp_path)), "--store", str(store)]) == 0

        assert "collectives are not counted as actors" in capsys.readouterr().err
        with Store(store) as opened:
            assert opened.get_setting(COLLECTIVES_ARE_ACTORS) is None

    def test_notes_go_to_stderr_so_json_stays_parseable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store = self._project(tmp_path)

        main(["ingest", str(_text_file(tmp_path)), "--store", str(store), "--json"])

        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "collectives" in captured.err

    def test_changing_the_answer_warns_that_it_breaks_comparison(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Changeable afterwards, but the tool that will later refuse to diff the snapshots
        should not be the first place anyone hears of it."""
        store = self._project(tmp_path)
        text = _text_file(tmp_path)
        main(["ingest", str(text), "--store", str(store), "--no-collectives-as-actors"])
        capsys.readouterr()

        assert main(["ingest", str(text), "--store", str(store), "--collectives-as-actors"]) == 0

        assert "collectives were not counted as actors" in capsys.readouterr().err

    def test_re_stating_the_same_answer_says_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store = self._project(tmp_path)
        text = _text_file(tmp_path)
        main(["ingest", str(text), "--store", str(store), "--collectives-as-actors"])
        capsys.readouterr()

        main(["ingest", str(text), "--store", str(store), "--collectives-as-actors"])

        assert "collectives were" not in capsys.readouterr().err


def test_omitting_model_reaches_the_provider_as_its_default() -> None:
    """The seam, not either side of it.

    The parser defaults --model to None and the provider has a default of its own; each
    was right alone, and together they sent a null model and earned a 400 from the API.
    Nothing that tested only one side could see it.
    """
    from dramatis.cli import _build_parser
    from dramatis.providers.anthropic_provider import DEFAULT_MODEL, AnthropicProvider

    parsed = _build_parser().parse_args(["analyse", "rev:abc"])

    assert AnthropicProvider(model=parsed.model).model == DEFAULT_MODEL


def test_analyze_is_accepted_as_a_spelling(tmp_path: Path) -> None:
    from dramatis.cli import _build_parser

    assert _build_parser().parse_args(["analyze", "rev:abc"]).revision == "rev:abc"


def test_analyse_rejects_an_unknown_effort() -> None:
    from dramatis.cli import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["analyse", "rev:abc", "--effort", "enormous"])


def test_analyse_takes_no_checkpoint_unless_asked(tmp_path: Path) -> None:
    """A checkpoint holds every prompt sent, so nothing is written beside a project
    without being asked for."""
    from dramatis.cli import _build_parser

    assert _build_parser().parse_args(["analyse", "rev:abc"]).checkpoint is None


def test_analyse_accepts_a_checkpoint_path(tmp_path: Path) -> None:
    from dramatis.cli import _build_parser

    checkpoint = tmp_path / "run.checkpoint.json"
    parsed = _build_parser().parse_args(["analyse", "rev:abc", "--checkpoint", str(checkpoint)])

    assert parsed.checkpoint == checkpoint


class TestTheCollectivesQuestionWhenNobodyCanAnswer:
    """`isatty` is not enough to know a person is there.

    A CI runner, an agent harness, an editor's terminal, or a redirect under a pty all
    report a tty and then answer EOF. Before this, that raised out of `input()` as a
    traceback in the middle of creating a project.
    """

    @staticmethod
    def _project(tmp_path: Path) -> Path:
        return tmp_path / "new.sqlite"

    def test_eof_takes_the_default_instead_of_raising(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        store = self._project(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _raise(EOFError))

        assert main(["ingest", str(_text_file(tmp_path)), "--store", str(store)]) == 0

        with Store(store) as opened:
            assert opened.get_setting(COLLECTIVES_ARE_ACTORS) is None

    def test_eof_says_which_default_it_took(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _raise(EOFError))

        main(["ingest", str(_text_file(tmp_path)), "--store", str(self._project(tmp_path))])

        assert "collectives are not counted as actors" in capsys.readouterr().err

    def test_the_note_is_the_same_sentence_either_way(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        # One fact, one phrasing. Two would drift the moment either was edited.
        from dramatis.cli import COLLECTIVES_DEFAULT_NOTE

        main(["ingest", str(_text_file(tmp_path)), "--store", str(tmp_path / "a.sqlite")])
        piped = capsys.readouterr().err

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _raise(EOFError))
        main(["ingest", str(_text_file(tmp_path)), "--store", str(tmp_path / "b.sqlite")])
        at_a_dead_terminal = capsys.readouterr().err

        assert COLLECTIVES_DEFAULT_NOTE in piped
        assert COLLECTIVES_DEFAULT_NOTE in at_a_dead_terminal

    def test_eof_keeps_json_on_stdout_parseable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _raise(EOFError))

        main(
            [
                "ingest",
                str(_text_file(tmp_path)),
                "--store",
                str(self._project(tmp_path)),
                "--json",
            ]
        )

        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "collectives" in captured.err

    def test_an_interrupt_aborts_rather_than_recording_a_decision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Somebody *is* there, and they interrupted the question.

        Reading that as "no" would record an answer they declined to give, on a setting
        that makes snapshots either side of it incomparable.
        """
        store = self._project(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", _raise(KeyboardInterrupt))

        with pytest.raises(SystemExit) as exit_code:
            main(["ingest", str(_text_file(tmp_path)), "--store", str(store)])

        assert exit_code.value.code == 130
        assert "aborted" in capsys.readouterr().err
        assert not store.exists(), "an aborted answer must not leave a project behind"

    def test_a_real_answer_is_still_honoured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._project(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda: "y")

        assert main(["ingest", str(_text_file(tmp_path)), "--store", str(store)]) == 0

        with Store(store) as opened:
            assert opened.get_setting(COLLECTIVES_ARE_ACTORS) is True


class TestAnalyseLike:
    """`--like` makes holding the analysis still the easy path.

    "I passed the same flags" is not the same claim as "the run recorded the same
    configuration", and the second is what a diff needs (D35).
    """

    def test_it_copies_the_settings_a_snapshot_recorded(self, monkeypatch) -> None:
        from dramatis.cli import _settings_like

        class FakeStore:
            def get_snapshot(self, identifier):
                return type("S", (), {"analysis_run_id": "run:1"})()

            def get_analysis_run(self, identifier):
                return {
                    "id": "run:1",
                    "parameters": {
                        "effort": "high",
                        "target_characters": 9000,
                        "max_rejection_rate": 0.1,
                        "weight_basis": "interaction_passages",
                    },
                }

        args = argparse.Namespace(like="snap:1", effort=None)
        settings = _settings_like(FakeStore(), args)

        assert settings == {
            "effort": "high",
            "target_characters": 9000,
            "max_rejection_rate": 0.1,
        }
        assert "weight_basis" not in settings, "an outcome is not a setting to copy"

    def test_an_explicit_effort_wins_and_says_it_broke_comparability(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        from dramatis.cli import _settings_like

        class FakeStore:
            def get_snapshot(self, identifier):
                return type("S", (), {"analysis_run_id": "run:1"})()

            def get_analysis_run(self, identifier):
                return {"id": "run:1", "parameters": {"effort": "high"}}

        args = argparse.Namespace(like="snap:1", effort="low")
        settings = _settings_like(FakeStore(), args)

        assert "effort" not in settings
        assert "will not be comparable" in capsys.readouterr().err

    def test_an_unknown_snapshot_is_refused(self) -> None:
        from dramatis.cli import _settings_like
        from dramatis.pipeline import PipelineError

        class FakeStore:
            def get_snapshot(self, identifier):
                return None

        with pytest.raises(PipelineError, match="no snapshot"):
            _settings_like(FakeStore(), argparse.Namespace(like="snap:gone", effort=None))

    def test_the_flag_is_offered_on_analyse(self) -> None:
        from dramatis.cli import _build_parser

        parsed = _build_parser().parse_args(["analyse", "rev:1", "--like", "snap:1"])
        assert parsed.like == "snap:1"


class TestReview:
    """`dramatis review` — showing and setting where review of a reading stands (5.1).

    Calls no model and reaches no network. Setting a status writes a decision beside the
    snapshot; the snapshot itself is never touched.
    """

    PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"

    def _analysed(self, tmp_path: Path):
        """A project holding one analysed work, and the snapshot it produced."""
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        source = tmp_path / "work.txt"
        source.write_text(self.PASSAGE, encoding="utf-8", newline="")
        store_path = tmp_path / "dramatis.sqlite"

        reply = json.dumps(
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
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ]
            }
        )

        with Store(store_path) as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            result = analyse(store, ingested.revision_id, ScriptedProvider([reply, grouping]))

        return store_path, result.snapshot

    def test_it_lists_every_subject_as_proposed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)

        assert main(["review", "--store", str(store_path)]) == 0

        out = capsys.readouterr().out
        assert snapshot.id in out
        assert "proposed" in out
        assert "0 ruled on by a person" in out

    def test_it_records_a_decision_and_reads_it_back(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]

        assert (
            main(
                [
                    "review",
                    "--store",
                    str(store_path),
                    "--character",
                    identifier,
                    "--status",
                    "accepted",
                ]
            )
            == 0
        )
        capsys.readouterr()

        assert main(["review", "--store", str(store_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        recorded = {(entry["kind"], entry["id"]): entry["status"] for entry in payload["subjects"]}
        assert recorded[("character", identifier)] == "accepted"
        assert payload["counts"]["accepted"] == 1

    def test_the_snapshot_is_not_touched(self, tmp_path: Path) -> None:
        store_path, snapshot = self._analysed(tmp_path)

        main(
            [
                "review",
                "--store",
                str(store_path),
                "--relation",
                snapshot.document["relations"][0]["id"],
                "--status",
                "rejected",
            ]
        )

        with Store(store_path) as store:
            after = store.get_snapshot(snapshot.id)
        assert after is not None
        assert after.sha256 == snapshot.sha256

    def test_a_correction_without_a_reason_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)

        code = main(
            [
                "review",
                "--store",
                str(store_path),
                "--character",
                snapshot.document["characters"][0]["id"],
                "--status",
                "corrected",
            ]
        )

        assert code == 1
        assert "must say what it corrects" in capsys.readouterr().err

    def test_a_status_with_nothing_named_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        assert main(["review", "--store", str(store_path), "--status", "accepted"]) == 1
        assert "--character ID or --relation ID" in capsys.readouterr().err

    def test_naming_both_a_node_and_an_edge_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        code = main(
            ["review", "--store", str(store_path), "--character", "char:a", "--relation", "rel:a"]
        )

        assert code == 1
        assert "not both" in capsys.readouterr().err

    def test_the_history_of_one_subject_keeps_what_it_superseded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]

        for status in ("accepted", "rejected"):
            main(
                [
                    "review",
                    "--store",
                    str(store_path),
                    "--character",
                    identifier,
                    "--status",
                    status,
                ]
            )
        capsys.readouterr()

        assert (
            main(
                [
                    "review",
                    "--store",
                    str(store_path),
                    "--character",
                    identifier,
                    "--history",
                    "--json",
                ]
            )
            == 0
        )
        past = json.loads(capsys.readouterr().out)
        assert [decision["status"] for decision in past] == ["accepted", "rejected"]

    def test_a_project_with_no_snapshot_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = tmp_path / "dramatis.sqlite"
        with Store(store_path):
            pass

        assert main(["review", "--store", str(store_path)]) == 1
        assert "no snapshot yet" in capsys.readouterr().err

    def test_an_unknown_status_is_refused_by_the_parser(self, tmp_path: Path) -> None:
        from dramatis.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["review", "--character", "char:a", "--status", "maybe"])

    def test_the_pending_filter_hides_what_has_been_ruled_on(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]
        main(
            ["review", "--store", str(store_path)]
            + ["--character", identifier, "--status", "accepted"]
        )
        capsys.readouterr()

        assert main(["review", "--store", str(store_path), "--pending"]) == 0
        listed = capsys.readouterr().out
        assert identifier not in listed
        assert snapshot.document["characters"][1]["id"] in listed


class TestCorrect:
    """`dramatis correct` — putting right what a reading got wrong (5.2).

    Calls no model and reaches no network. Recording a correction changes no stored snapshot;
    it is written into the graph by the next analysis, and the command says so.
    """

    PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"

    def _analysed(self, tmp_path: Path):
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        source = tmp_path / "work.txt"
        source.write_text(self.PASSAGE, encoding="utf-8", newline="")
        store_path = tmp_path / "dramatis.sqlite"

        reply = json.dumps(
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
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ]
            }
        )

        with Store(store_path) as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            result = analyse(store, ingested.revision_id, ScriptedProvider([reply, grouping]))

        return store_path, result.snapshot

    def test_it_records_a_correction_and_says_it_applies_next_time(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--character", identifier, "--field", "name", "--value", "Ada Mbeki"]
        )

        assert code == 0
        out = capsys.readouterr().out
        assert "Ada Mbeki" in out
        assert "applies to the next analysis" in out

    def test_a_list_field_takes_several_words(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--character", identifier, "--field", "aliases", "--value", "Lizzy", "Eliza"]
            + ["--json"]
        )

        assert code == 0
        assert json.loads(capsys.readouterr().out)["value"] == ["Lizzy", "Eliza"]

    def test_a_number_is_stored_as_a_number(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["relations"][0]["id"]

        main(
            ["correct", "--store", str(store_path)]
            + ["--relation", identifier, "--field", "valence", "--value", "-0.4", "--json"]
        )

        assert json.loads(capsys.readouterr().out)["value"] == -0.4

    def test_a_field_that_takes_one_value_refuses_several(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--character", identifier, "--field", "name", "--value", "Ada", "Mbeki"]
        )

        assert code == 1
        assert "takes one value" in capsys.readouterr().err

    def test_a_field_that_may_not_be_corrected_says_why(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["relations"][0]["id"]

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--relation", identifier, "--field", "evidence", "--value", "anything"]
        )

        assert code == 1
        # The reason, not "invalid choice": reaching for evidence is a sensible thing to want
        # and why it is declined is the useful half of the answer.
        assert "Invariant 3" in capsys.readouterr().err

    def test_a_value_without_a_field_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)

        code = main(
            ["correct", "--store", str(store_path)] + ["--field", "name", "--value", "Ada Mbeki"]
        )

        assert code == 1
        assert "--character ID or --relation ID" in capsys.readouterr().err

    def test_it_lists_what_stands(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]
        main(
            ["correct", "--store", str(store_path)]
            + ["--character", identifier, "--field", "name", "--value", "Ada Mbeki"]
        )
        capsys.readouterr()

        assert main(["correct", "--store", str(store_path)]) == 0
        out = capsys.readouterr().out
        assert "1 standing correction" in out
        assert "Ada Mbeki" in out

    def test_the_history_of_one_subject_keeps_what_it_superseded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, snapshot = self._analysed(tmp_path)
        identifier = snapshot.document["characters"][0]["id"]
        for name in ("Ada Mbeki", "Ada M. Mbeki"):
            main(
                ["correct", "--store", str(store_path)]
                + ["--character", identifier, "--field", "name", "--value", name]
            )
        capsys.readouterr()

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--character", identifier, "--history", "--json"]
        )

        assert code == 0
        past = json.loads(capsys.readouterr().out)
        assert [entry["value"] for entry in past] == ["Ada Mbeki", "Ada M. Mbeki"]

    def test_a_project_with_no_snapshot_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = tmp_path / "dramatis.sqlite"
        with Store(store_path):
            pass

        assert main(["correct", "--store", str(store_path)]) == 1
        assert "no snapshot yet" in capsys.readouterr().err

    def test_naming_both_a_node_and_an_edge_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        code = main(
            ["correct", "--store", str(store_path)]
            + ["--character", "char:a", "--relation", "rel:a"]
        )

        assert code == 1
        assert "not both" in capsys.readouterr().err


class TestMergeAndSplit:
    """`dramatis merge` and `dramatis split` — deciding who is who (5.3).

    Neither calls a model and neither rewrites a snapshot: the registry is the mechanism, and
    the next analysis reads it.
    """

    PASSAGE = "Ada met Bram at the gate.\n\nMiss Ada did not answer Bram.\n"
    NAMES = ("Ada", "Miss Ada", "Bram")

    def _analysed(self, tmp_path: Path):
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        source = tmp_path / "work.txt"
        source.write_text(self.PASSAGE, encoding="utf-8", newline="")
        store_path = tmp_path / "dramatis.sqlite"

        reply = json.dumps(
            {
                "characters": [{"name": n, "aliases": [], "kind": "person"} for n in self.NAMES],
                "interactions": [
                    {
                        "participants": ["Ada", "Bram"],
                        "quotation": "Ada met Bram at the gate.",
                        "note": "",
                    },
                    {
                        "participants": ["Miss Ada", "Bram"],
                        "quotation": "Miss Ada did not answer Bram.",
                        "note": "",
                    },
                ],
            }
        )
        grouping = json.dumps(
            {
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in self.NAMES
                ]
            }
        )

        with Store(store_path) as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            analyse(store, ingested.revision_id, ScriptedProvider([reply, grouping]))

        return store_path

    def test_it_merges_and_says_what_the_survivor_answers_to(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)

        code = main(["merge", "char:miss-ada", "--into", "char:ada", "--store", str(store_path)])

        assert code == 0
        out = capsys.readouterr().out
        assert "merged char:miss-ada into char:ada" in out
        assert "Miss Ada" in out
        assert "the next analysis" in out

    def test_the_decision_is_in_the_registry_afterwards(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)
        main(["merge", "char:miss-ada", "--into", "char:ada", "--store", str(store_path)])
        capsys.readouterr()

        assert main(["characters", "--store", str(store_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert [entry["action"] for entry in payload["decisions"]] == ["merge"]
        assert payload["retired"][0]["id"] == "char:miss-ada"

    def test_merging_into_itself_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)

        code = main(["merge", "char:ada", "--into", "char:ada", "--store", str(store_path)])

        assert code == 1
        assert "into itself" in capsys.readouterr().err

    def test_an_unknown_character_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)

        code = main(["merge", "char:nobody", "--into", "char:ada", "--store", str(store_path)])

        assert code == 1
        assert "not a character" in capsys.readouterr().err

    def test_it_splits_on_the_forms_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)
        main(["merge", "char:miss-ada", "--into", "char:ada", "--store", str(store_path)])
        capsys.readouterr()

        code = main(
            ["split", "char:ada", "--form", "Miss Ada", "--store", str(store_path), "--json"]
        )

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["action"] == "split"
        assert payload["forms"] == ["Miss Ada"]

    def test_splitting_every_form_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._analysed(tmp_path)

        code = main(["split", "char:ada", "--form", "Ada", "--store", str(store_path)])

        assert code == 1
        assert "rename, not a split" in capsys.readouterr().err

    def test_split_needs_a_form(self, tmp_path: Path) -> None:
        from dramatis.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["split", "char:ada"])


class TestContinuity:
    """`dramatis continuity` — what the corpus no longer agrees with itself about (5.4).

    Calls no model and changes nothing.
    """

    def _analysed(self, tmp_path: Path):
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        source = tmp_path / "work.txt"
        source.write_text(
            "Ada met Bram at the gate.\n\nBram did not answer her.\n",
            encoding="utf-8",
            newline="",
        )
        store_path = tmp_path / "dramatis.sqlite"

        payload = json.dumps(
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
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ],
            }
        )

        with Store(store_path) as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            analyse(store, ingested.revision_id, ScriptedProvider(lambda _r: payload))

        return store_path, source

    def test_a_reading_of_the_current_text_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        assert main(["continuity", "--store", str(store_path)]) == 0

        out = capsys.readouterr().out
        assert "0 finding(s)" in out
        # Not an empty report: "checked and clean" and "nothing to compare" are different
        # answers and only one of them is true here.
        assert "the reading is of the current text" in out

    def test_it_reports_a_name_the_work_has_moved_on_from(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The finding is between documents, which is the shape the mistake has: a rename is
        a find-and-replace in the file being worked on, and what it misses is another file."""
        from dramatis.ingest import ingest_folder
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        root = tmp_path / "corpus"
        root.mkdir()
        one = root / "one.md"
        two = root / "two.md"
        one.write_text("Ada met Bram at the gate.\n", encoding="utf-8", newline="")
        two.write_text("Bram did not answer Ada.\n", encoding="utf-8", newline="")

        store_path = tmp_path / "dramatis.sqlite"
        payload = json.dumps(
            {
                "characters": [
                    {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram")
                ],
                "interactions": [],
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ],
            }
        )
        with Store(store_path) as store:
            ingested = ingest_folder(store, root, work_title="A Work", collection_name="A")
            analyse(store, ingested.revision_id, ScriptedProvider(lambda _r: payload))

            # Renamed in one file and missed in the other.
            two.write_text("Kell did not answer Ada.\n", encoding="utf-8", newline="")
            ingest_folder(store, root, work_title="A Work", collection_name="A")

        assert main(["continuity", "--store", str(store_path)]) == 0

        out = capsys.readouterr().out
        assert "names the work has moved on from" in out
        assert "Bram" in out
        assert "one.md" in out

    def test_json_carries_the_axes_it_compared(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        assert main(["continuity", "--store", str(store_path), "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["unchanged"] is True
        assert payload["read_revision"] == payload["against_revision"]

    def test_an_unknown_revision_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        code = main(["continuity", "--store", str(store_path), "--against", "rev:nothing"])

        assert code == 1
        assert "no text revision" in capsys.readouterr().err

    def test_a_project_with_no_work_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = tmp_path / "dramatis.sqlite"
        with Store(store_path):
            pass

        assert main(["continuity", "--store", str(store_path)]) == 1
        assert "no work yet" in capsys.readouterr().err


class TestExport:
    """`dramatis export` — handing a reading to somebody else's tool (6.1).

    Calls no model and reaches no network, and writes nothing into the project. What is
    tested here is the command, not the formats — `tests/test_export.py` holds those.
    """

    def _analysed(self, tmp_path: Path):
        from dramatis.ingest import ingest_file
        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        source = tmp_path / "work.txt"
        source.write_text(
            "Ada met Bram at the gate.\n\nBram did not answer her.\n",
            encoding="utf-8",
            newline="",
        )
        store_path = tmp_path / "dramatis.sqlite"

        payload = json.dumps(
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
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Bram")
                ],
            }
        )

        with Store(store_path) as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            analyse(store, ingested.revision_id, ScriptedProvider(lambda _r: payload))

        return store_path, source

    def test_it_writes_the_format_it_was_asked_for(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        assert main(["export", "gexf", "--store", str(store_path)]) == 0

        assert "<gexf" in capsys.readouterr().out

    def test_without_a_snapshot_it_takes_the_newest_and_says_which(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """To stderr, so a piped export stays a valid file, and always: citing a reading you
        are not looking at is the mistake this prevents."""
        store_path, _ = self._analysed(tmp_path)

        assert main(["export", "jsonld", "--store", str(store_path)]) == 0

        captured = capsys.readouterr()
        assert "note: exporting snap:" in captured.err
        assert json.loads(captured.out)["id"] in captured.err

    def test_an_unknown_snapshot_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)

        code = main(["export", "gexf", "--store", str(store_path), "--snapshot", "snap:nothing"])

        assert code == 1
        assert "no snapshot snap:nothing" in capsys.readouterr().err

    def test_a_project_with_no_reading_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = tmp_path / "dramatis.sqlite"
        with Store(store_path):
            pass

        assert main(["export", "graphml", "--store", str(store_path)]) == 1
        assert "no reading to export" in capsys.readouterr().err

    def test_output_gains_the_extension_it_is_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path, _ = self._analysed(tmp_path)
        target = tmp_path / "out" / "graph"

        assert main(["export", "graphml", "--store", str(store_path), "-o", str(target)]) == 0

        written = target.with_suffix(".graphml")
        assert written.is_file()
        assert str(written) in capsys.readouterr().out

    def test_output_that_already_has_it_does_not_get_it_twice(self, tmp_path: Path) -> None:
        store_path, _ = self._analysed(tmp_path)
        target = tmp_path / "graph.gexf"

        assert main(["export", "gexf", "--store", str(store_path), "-o", str(target)]) == 0

        assert target.is_file()
        assert not (tmp_path / "graph.gexf.gexf").exists()

    def test_csv_writes_two_files_named_apart(self, tmp_path: Path) -> None:
        store_path, _ = self._analysed(tmp_path)

        assert main(["export", "csv", "--store", str(store_path), "-o", str(tmp_path / "g")]) == 0

        assert (tmp_path / "g.nodes.csv").is_file()
        assert (tmp_path / "g.edges.csv").is_file()

    def test_csv_to_stdout_is_refused_rather_than_run_together(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Two files cannot share one stream, and concatenating them would produce a third
        thing that is neither list."""
        store_path, _ = self._analysed(tmp_path)

        code = main(["export", "csv", "--store", str(store_path)])

        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == ""
        assert "--output" in captured.err

    def test_a_standing_review_decision_reaches_the_export(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """5.1: the decision is beside the snapshot, and the export is the copy that gets
        cited. Reading the document alone would publish a character somebody has rejected."""
        store_path, _ = self._analysed(tmp_path)
        with Store(store_path) as store:
            work = store.list_works()[0]
            snapshot = store.list_snapshots(work["id"])[-1]
        identifier = snapshot.document["characters"][0]["id"]

        assert (
            main(
                [
                    "review",
                    "--store",
                    str(store_path),
                    "--character",
                    identifier,
                    "--status",
                    "rejected",
                ]
            )
            == 0
        )
        capsys.readouterr()

        assert main(["export", "jsonld", "--store", str(store_path)]) == 0
        rendered = json.loads(capsys.readouterr().out)

        statuses = {entry["id"]: entry.get("review_status") for entry in rendered["characters"]}
        assert statuses[identifier] == "rejected"

    def test_it_leaves_the_snapshot_alone(self, tmp_path: Path) -> None:
        store_path, _ = self._analysed(tmp_path)
        with Store(store_path) as store:
            before = store.list_snapshots(store.list_works()[0]["id"])[-1]

        assert main(["export", "graphml", "--store", str(store_path)]) == 0

        with Store(store_path) as store:
            after = store.get_snapshot(before.id)
        assert after is not None
        assert after.sha256 == before.sha256

    def test_annotations_writes_the_evidence_the_graph_formats_only_counted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """6.2. The quotation is in exactly one export, and this is it."""
        store_path, _ = self._analysed(tmp_path)

        assert main(["export", "annotations", "--store", str(store_path)]) == 0
        rendered = json.loads(capsys.readouterr().out)

        assert rendered["type"] == "AnnotationCollection"
        assert rendered["total"] == 1
        target = rendered["first"]["items"][0]["target"]
        assert target["selector"]["type"] == "TextQuoteSelector"
        assert target["selector"]["exact"] == "Ada met Bram at the gate."

    def test_the_annotations_file_is_named_apart_from_the_graph_jsonld(
        self, tmp_path: Path
    ) -> None:
        """Both are JSON-LD and they are not the same document. One name for two files is
        one of them silently overwriting the other."""
        store_path, _ = self._analysed(tmp_path)

        for fmt in ("jsonld", "annotations"):
            assert main(["export", fmt, "--store", str(store_path), "-o", str(tmp_path / "g")]) == 0

        assert (tmp_path / "g.jsonld").is_file()
        assert (tmp_path / "g.annotations.jsonld").is_file()


class TestImport:
    """`dramatis import` — reading a document another tool produced (6.3).

    Calls no model and reaches no network. Writes nothing unless the whole document passes.
    """

    def _document(self, tmp_path: Path) -> Path:
        source = tmp_path / "reading.dramatis.json"
        source.write_text(json.dumps(minimal_document()), encoding="utf-8")
        return source

    def test_it_reads_a_document_into_a_project_that_does_not_exist_yet(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Like ingest, and unlike every other command: somebody handed a file has no project
        to import it into yet, and telling them to make one first is a step for nothing."""
        source = self._document(tmp_path)
        store_path = tmp_path / "new.sqlite"

        assert main(["import", str(source), "--store", str(store_path)]) == 0

        assert "imported snap:1" in capsys.readouterr().out
        with Store(store_path) as store:
            assert store.get_snapshot("snap:1") is not None

    def test_it_says_the_text_did_not_come_with_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Not a warning: it is what the format is for. A reader who does not know will think
        the evidence is broken the first time a passage will not open."""
        source = self._document(tmp_path)

        assert main(["import", str(source), "--store", str(tmp_path / "new.sqlite")]) == 0

        err = capsys.readouterr().err
        assert "1 document(s) recorded without their text" in err
        assert "dramatis ingest" in err

    def test_notes_go_to_stderr_so_json_stays_parseable(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        source = self._document(tmp_path)

        code = main(["import", str(source), "--store", str(tmp_path / "new.sqlite"), "--json"])

        captured = capsys.readouterr()
        assert code == 0
        payload = json.loads(captured.out)
        assert payload["snapshot_id"] == "snap:1"
        assert payload["characters"] == 2
        assert captured.err

    def test_a_refusal_exits_one_and_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        broken = minimal_document()
        broken["relations"][0]["source"] = "char:missing"
        source = tmp_path / "broken.json"
        source.write_text(json.dumps(broken), encoding="utf-8")
        store_path = tmp_path / "new.sqlite"

        code = main(["import", str(source), "--store", str(store_path)])

        assert code == 1
        assert "schema" in capsys.readouterr().err
        with Store(store_path) as store:
            assert store.count("characters") == 0

    def test_the_round_trip_the_phase_is_accepted_on(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Export, import into a different project, export again, compare bytes."""
        source = self._document(tmp_path)
        first = tmp_path / "one.sqlite"
        second = tmp_path / "two.sqlite"

        assert main(["import", str(source), "--store", str(first)]) == 0
        assert main(["export", "snapshot", "--store", str(first), "-o", str(tmp_path / "a")]) == 0
        assert main(["import", str(tmp_path / "a.dramatis.json"), "--store", str(second)]) == 0
        assert main(["export", "snapshot", "--store", str(second), "-o", str(tmp_path / "b")]) == 0
        capsys.readouterr()

        assert (tmp_path / "a.dramatis.json").read_bytes() == (
            tmp_path / "b.dramatis.json"
        ).read_bytes()


class TestEditionsAndCorrespondence:
    """`dramatis ingest --edition` and `dramatis correspond` (6.4).

    Neither calls a model nor reaches a network.
    """

    def _two_editions(self, tmp_path: Path) -> Path:
        store_path = tmp_path / "d.sqlite"
        for edition, confidante in (("1889-first", "Hesper"), ("1903-revised", "Perdita")):
            source = tmp_path / f"{edition}.md"
            source.write_text(
                f"Corin Ashe found {confidante} waiting.\n", encoding="utf-8", newline=""
            )
            assert (
                main(
                    [
                        "ingest",
                        str(source),
                        "--store",
                        str(store_path),
                        "--work",
                        "The Salt Road",
                        "--collection",
                        "Salt Road",
                        "--edition",
                        edition,
                    ]
                )
                == 0
            )
        return store_path

    def test_two_editions_become_two_works_in_one_collection(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._two_editions(tmp_path)
        capsys.readouterr()

        assert main(["status", "--store", str(store_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert len(payload["collections"]) == 1
        assert {work["edition"] for work in payload["works"]} == {"1889-first", "1903-revised"}
        assert {work["id"] for work in payload["works"]} == {
            "work:the-salt-road@1889-first",
            "work:the-salt-road@1903-revised",
        }

    def test_status_names_the_edition_beside_the_title(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Two rows differing only in an identifier suffix is how somebody reads one
        edition's numbers as the other's."""
        store_path = self._two_editions(tmp_path)
        capsys.readouterr()

        assert main(["status", "--store", str(store_path)]) == 0

        out = capsys.readouterr().out
        assert "[1889-first]" in out
        assert "[1903-revised]" in out

    def _registered(self, tmp_path: Path) -> Path:
        store_path = tmp_path / "reg.sqlite"
        with Store(store_path) as store:
            from dramatis.store import RegisteredCharacter

            store.upsert_collection("col:salt-road", "Salt Road")
            for identifier, name in (("char:hesper", "Hesper"), ("char:perdita", "Perdita")):
                store.upsert_character(
                    RegisteredCharacter(id=identifier, collection_id="col:salt-road", name=name)
                )
        return store_path

    def test_it_records_a_correspondence_and_reads_it_back(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._registered(tmp_path)

        assert (
            main(
                [
                    "correspond",
                    "char:hesper",
                    "char:perdita",
                    "--store",
                    str(store_path),
                    "--note",
                    "renamed in 1903",
                ]
            )
            == 0
        )
        assert "one figure across editions" in capsys.readouterr().out

        assert main(["correspond", "--store", str(store_path), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload == [
            {
                "left": "char:hesper",
                "right": "char:perdita",
                "note": "renamed in 1903",
                "decided_at": payload[0]["decided_at"],
            }
        ]

    def test_it_says_neither_character_was_changed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """The operation next to this one is destructive, and a reader should not have to
        remember which this was."""
        store_path = self._registered(tmp_path)

        main(["correspond", "char:hesper", "char:perdita", "--store", str(store_path)])

        assert "neither character was changed" in capsys.readouterr().err

    def test_listing_an_empty_registry_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._registered(tmp_path)

        assert main(["correspond", "--store", str(store_path)]) == 0
        assert "no cross-edition correspondences" in capsys.readouterr().out

    def test_one_character_alone_is_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._registered(tmp_path)

        code = main(["correspond", "char:hesper", "--store", str(store_path)])

        assert code == 1
        assert "needs two characters" in capsys.readouterr().err

    def test_it_can_be_withdrawn(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        store_path = self._registered(tmp_path)
        main(["correspond", "char:hesper", "char:perdita", "--store", str(store_path)])
        capsys.readouterr()

        assert (
            main(
                [
                    "correspond",
                    "char:hesper",
                    "char:perdita",
                    "--store",
                    str(store_path),
                    "--withdraw",
                ]
            )
            == 0
        )
        assert "withdrew" in capsys.readouterr().out

    def test_withdrawing_one_that_is_not_there_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store_path = self._registered(tmp_path)

        code = main(
            [
                "correspond",
                "char:hesper",
                "char:perdita",
                "--store",
                str(store_path),
                "--withdraw",
            ]
        )

        assert code == 1
        assert "no correspondence" in capsys.readouterr().err

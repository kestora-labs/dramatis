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

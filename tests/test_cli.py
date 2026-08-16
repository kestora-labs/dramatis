"""Tests for the ``dramatis`` command line interface.

Exit codes are the contract: 0 means every document passed, non-zero means at least one
did not. Anything scripting Dramatis in CI depends on that more than on the wording of the
messages.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.cli import main
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

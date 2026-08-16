"""Tests for finding the project file.

The failure this exists to prevent: a command run one folder over does not fail, it makes a
second empty project and reports success. So the two properties under test are that a
project is found from anywhere inside it, and that a read never brings one into existence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.cli import main
from dramatis.locate import (
    STORE_FILENAME,
    StoreLocation,
    StoreNotFound,
    find_upwards,
    resolve_store,
)
from dramatis.store import Store


def a_project(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STORE_FILENAME
    with Store(path):
        pass
    return path


def a_text(directory: Path, name: str = "work.txt", body: str = "Ada met Bram.\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


# -- discovery ------------------------------------------------------------------------------


class TestDiscovery:
    def test_a_project_in_the_current_directory_is_found(self, tmp_path: Path) -> None:
        a_project(tmp_path)
        location = resolve_store(start=tmp_path)

        assert location.exists and location.path == tmp_path / STORE_FILENAME

    def test_a_project_above_is_found(self, tmp_path: Path) -> None:
        """A command should work anywhere inside a project, as git does."""
        a_project(tmp_path)
        deep = tmp_path / "drafts" / "chapters"
        deep.mkdir(parents=True)

        location = resolve_store(start=deep)

        assert location.exists and location.path == tmp_path / STORE_FILENAME
        assert "searching upward" in location.how

    def test_the_nearest_project_wins(self, tmp_path: Path) -> None:
        a_project(tmp_path)
        nearer = a_project(tmp_path / "inner")

        assert resolve_store(start=tmp_path / "inner").path == nearer

    def test_nothing_found_reports_where_it_would_go(self, tmp_path: Path) -> None:
        location = resolve_store(start=tmp_path)

        assert not location.exists
        assert location.path == tmp_path / STORE_FILENAME
        assert "would be created" in location.how

    def test_an_explicit_path_is_never_searched_for(self, tmp_path: Path) -> None:
        """Being sent somewhere unexpected because a file sat in a parent would be worse
        than the problem discovery solves."""
        a_project(tmp_path)
        named = tmp_path / "inner" / "elsewhere.sqlite"

        location = resolve_store(named, start=tmp_path / "inner")

        assert location.path == named
        assert location.explicit and not location.exists
        assert location.how == "named on the command line"

    def test_find_upwards_returns_none_when_there_is_nothing(self, tmp_path: Path) -> None:
        assert find_upwards(tmp_path) is None


class TestRequire:
    def test_an_existing_project_is_returned(self, tmp_path: Path) -> None:
        path = a_project(tmp_path)
        assert resolve_store(start=tmp_path).require() == path

    def test_a_missing_project_explains_where_it_looked(self, tmp_path: Path) -> None:
        with pytest.raises(StoreNotFound) as failure:
            resolve_store(start=tmp_path).require()

        message = str(failure.value)
        assert str(tmp_path) in message
        assert "dramatis ingest" in message

    def test_a_missing_named_project_says_to_check_the_path(self, tmp_path: Path) -> None:
        with pytest.raises(StoreNotFound, match="Check the path"):
            resolve_store(tmp_path / "absent.sqlite").require()

    def test_the_location_is_inspectable_without_raising(self, tmp_path: Path) -> None:
        location = StoreLocation(path=tmp_path / "x.sqlite", exists=False, explicit=True)
        assert location.exists is False


# -- reads never create -----------------------------------------------------------------------


class TestReadsNeverCreate:
    def test_analyse_refuses_rather_than_creating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """The original failure: a command reporting success for work it did not do."""
        monkeypatch.chdir(tmp_path)

        assert main(["analyse", "rev:abc"]) == 1
        assert not (tmp_path / STORE_FILENAME).exists(), "a read created a project"
        assert "no Dramatis project" in capsys.readouterr().err

    def test_serve_refuses_rather_than_creating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)

        assert main(["serve"]) == 1
        assert not (tmp_path / STORE_FILENAME).exists()
        assert "no Dramatis project" in capsys.readouterr().err

    def test_status_refuses_rather_than_creating(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)

        assert main(["status"]) == 1
        assert not (tmp_path / STORE_FILENAME).exists()

    def test_ingest_is_the_one_command_that_may_create(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        source = a_text(tmp_path)

        assert main(["ingest", str(source)]) == 0
        assert (tmp_path / STORE_FILENAME).is_file()
        assert "new project" in capsys.readouterr().out

    def test_ingest_from_a_subdirectory_joins_the_project_above(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """The whole point: the second work lands in the project, not beside it."""
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path, "first.txt", "One.\n")), "--work", "First"])
        capsys.readouterr()

        deep = tmp_path / "drafts"
        deep.mkdir()
        monkeypatch.chdir(deep)
        assert main(["ingest", str(a_text(deep, "second.txt", "Two.\n")), "--work", "Second"]) == 0

        assert not (deep / STORE_FILENAME).exists(), "a second project was created"
        with Store(tmp_path / STORE_FILENAME) as store:
            assert {work["title"] for work in store.list_works()} == {"First", "Second"}


# -- status ------------------------------------------------------------------------------------


class TestStatus:
    def test_it_reports_the_project_and_how_it_was_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work", "--collection", "A Universe"])
        capsys.readouterr()

        assert main(["status"]) == 0
        out = capsys.readouterr().out

        assert str(tmp_path / STORE_FILENAME) in out
        assert "A Universe" in out
        assert "A Work" in out
        assert "1 revision(s)" in out

    def test_it_works_from_a_subdirectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work"])
        capsys.readouterr()

        deep = tmp_path / "drafts"
        deep.mkdir()
        monkeypatch.chdir(deep)

        assert main(["status"]) == 0
        assert "searching upward" in capsys.readouterr().out

    def test_an_empty_project_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        a_project(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert main(["status"]) == 0
        assert "nothing ingested yet" in capsys.readouterr().out

    def test_it_reports_the_settings_the_project_is_studied_under(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work"])
        with Store(tmp_path / STORE_FILENAME) as store:
            store.set_setting("collectives_are_actors", True)
        capsys.readouterr()

        assert main(["status"]) == 0
        out = capsys.readouterr().out

        assert "collectives_are_actors = true" in out, "a setting must read as its own type"

    def test_settings_are_shown_before_anything_is_ingested(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """A project may hold the terms of the study before it holds a word of text, and
        `status` returns early once it has said nothing is ingested."""
        a_project(tmp_path)
        with Store(tmp_path / STORE_FILENAME) as store:
            store.set_setting("collectives_are_actors", False)
        monkeypatch.chdir(tmp_path)

        assert main(["status"]) == 0
        out = capsys.readouterr().out

        assert "collectives_are_actors = false" in out
        assert "nothing ingested yet" in out

    def test_a_project_with_no_settings_says_nothing_about_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work"])
        capsys.readouterr()

        assert main(["status"]) == 0
        assert "settings" not in capsys.readouterr().out

    def test_json_output_is_machine_readable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work", "--collection", "A Universe"])
        capsys.readouterr()

        assert main(["status", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert payload["collections"] == [{"id": "col:a-universe", "name": "A Universe"}]
        assert payload["works"][0]["title"] == "A Work"
        assert payload["works"][0]["revisions"] == 1
        assert payload["works"][0]["snapshots"] == []
        assert payload["store_version"] >= 3
        assert payload["settings"] == {}

    def test_it_counts_the_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        from dramatis.store import RegisteredCharacter

        monkeypatch.chdir(tmp_path)
        main(["ingest", str(a_text(tmp_path)), "--work", "A Work", "--collection", "A Universe"])
        capsys.readouterr()

        with Store(tmp_path / STORE_FILENAME) as store:
            store.upsert_character(
                RegisteredCharacter(id="char:ada", collection_id="col:a-universe", name="Ada")
            )

        assert main(["status", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["characters"] == 1

"""Tests for the local server.

The property under test is that the API hands back the *stored* document — not a reshaped
view of it. A second representation of the same graph is a second place for the truth to
live, and the two drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.ingest import ingest_file
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.schema import DOCUMENT_VERSION
from dramatis.server import DEFAULT_HOST, DEFAULT_PORT, create_app
from dramatis.store import Store

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

PASSAGE = "Ada met Bram at the gate.\n\nBram did not answer her.\n\nCai spoke to Ada alone.\n"


def a_reply() -> str:
    return json.dumps(
        {
            "characters": [
                {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Bram", "Cai")
            ],
            "interactions": [
                {
                    "participants": ["Ada", "Bram"],
                    "quotation": "Ada met Bram at the gate.",
                    "note": "",
                },
                {
                    "participants": ["Ada", "Cai"],
                    "quotation": "Cai spoke to Ada alone.",
                    "note": "",
                },
            ],
        }
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


@pytest.fixture
def analysed(tmp_path: Path):
    """A project file with one snapshot in it, plus the document that was stored."""
    source = tmp_path / "work.txt"
    source.write_text(PASSAGE, encoding="utf-8", newline="")
    store_path = tmp_path / "project.sqlite"

    with Store(store_path) as store:
        ingested = ingest_file(store, source, work_title="A Work", collection_name="A Collection")
        result = analyse(store, ingested.revision_id, ScriptedProvider([a_reply(), a_grouping()]))
        document = result.snapshot.document
        snapshot_id = result.snapshot.id

    return store_path, snapshot_id, document


@pytest.fixture
def client(analysed):
    store_path, _, _ = analysed
    with TestClient(create_app(store_path)) as opened:
        yield opened


class TestHealth:
    def test_it_reports_versions_without_a_store(self, tmp_path: Path) -> None:
        with TestClient(create_app(tmp_path / "absent.sqlite")) as client:
            payload = client.get("/api/health").json()

        assert payload["schema_version"] == DOCUMENT_VERSION
        assert payload["store_present"] is False

    def test_it_reports_a_present_store(self, client) -> None:
        assert client.get("/api/health").json()["store_present"] is True


class TestSnapshots:
    def test_the_document_is_returned_unchanged(self, analysed, client) -> None:
        """Byte for byte what was archived — no view model, no computed fields."""
        _, snapshot_id, document = analysed

        served = client.get(f"/api/snapshots/{snapshot_id}").json()

        assert served == document

    def test_the_served_document_still_validates(self, client, analysed) -> None:
        from dramatis.validation import validate_document

        _, snapshot_id, _ = analysed
        served = client.get(f"/api/snapshots/{snapshot_id}").json()

        assert validate_document(served) == []

    def test_listing_summarises_without_replacing(self, client, analysed) -> None:
        _, snapshot_id, document = analysed

        listed = client.get("/api/snapshots").json()

        assert len(listed) == 1
        assert listed[0]["id"] == snapshot_id
        assert listed[0]["characters"] == len(document["characters"])
        assert listed[0]["relations"] == len(document["relations"])
        assert "relations" in listed[0] and "characters" in listed[0]
        assert "evidence" not in json.dumps(listed[0]), "the list is a summary, not a graph"

    def test_both_axes_are_in_the_summary(self, client) -> None:
        listed = client.get("/api/snapshots").json()[0]

        assert listed["text_revision_id"].startswith("rev:")
        assert listed["analysis_run_id"].startswith("run:")

    def test_an_unknown_snapshot_is_a_404(self, client) -> None:
        assert client.get("/api/snapshots/snap:nope").status_code == 404

    def test_filtering_by_work(self, client, analysed) -> None:
        _, _, document = analysed
        work_id = document["works"][0]["id"]

        assert len(client.get(f"/api/snapshots?work_id={work_id}").json()) == 1
        assert client.get("/api/snapshots?work_id=work:nope").json() == []

    def test_a_missing_project_file_is_a_404_not_a_crash(self, tmp_path: Path) -> None:
        with TestClient(create_app(tmp_path / "absent.sqlite")) as client:
            assert client.get("/api/snapshots").status_code == 404


class TestWorks:
    def test_works_are_listed(self, client) -> None:
        works = client.get("/api/works").json()

        assert len(works) == 1
        assert works[0]["title"] == "A Work"


class TestBinding:
    def test_it_defaults_to_the_loopback_interface(self) -> None:
        """A project file holds unpublished work; serving it to the LAN by default would
        put a manuscript on the office network because someone typed a command."""
        assert DEFAULT_HOST == "127.0.0.1"
        assert DEFAULT_PORT == 7373


class TestOptionalDependency:
    def test_the_framework_is_not_required_to_use_dramatis(self) -> None:
        """Invariant 6: reading and validating a project must work without the server."""
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "for name in ('fastapi', 'uvicorn'):\n"
                "    sys.modules[name] = None\n"
                "import dramatis, dramatis.validation, dramatis.store, dramatis.pipeline\n"
                "import dramatis.server\n"
                "print('ok')\n",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_a_missing_framework_explains_how_to_install_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import builtins

        from dramatis.server import ServerError

        real_import = builtins.__import__

        def refuse(name: str, *args, **kwargs):
            if name == "fastapi":
                raise ModuleNotFoundError("No module named 'fastapi'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

        with pytest.raises(ServerError, match=r"dramatis\[serve\]"):
            create_app(tmp_path / "project.sqlite")

    def test_the_banner_is_not_printed_when_the_server_cannot_start(
        self, monkeypatch: pytest.MonkeyPatch, analysed, capsys: pytest.CaptureFixture
    ) -> None:
        """Announcing an address and then failing leaves a lie as the last line on screen."""
        import builtins

        from dramatis.cli import main

        store_path, _, _ = analysed
        real_import = builtins.__import__

        def refuse(name: str, *args, **kwargs):
            if name == "uvicorn":
                raise ModuleNotFoundError("No module named 'uvicorn'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

        assert main(["serve", "--store", str(store_path)]) == 1

        captured = capsys.readouterr()
        assert "Dramatis on http://" not in captured.out
        assert "uvicorn" in captured.err

    def test_building_the_app_does_not_need_the_asgi_server(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Only listening on a port needs uvicorn; building the app is all a test does."""
        import builtins

        real_import = builtins.__import__

        def refuse(name: str, *args, **kwargs):
            if name == "uvicorn":
                raise ModuleNotFoundError("No module named 'uvicorn'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

        assert create_app(tmp_path / "project.sqlite") is not None

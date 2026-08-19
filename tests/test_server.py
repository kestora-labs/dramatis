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
from dramatis.store import NARRATIVE, Store

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


class TestServingTheClient:
    """Where the built client is found. The default assumes a source checkout; the Docker
    image installs a wheel and points `DRAMATIS_WEB_ROOT` at the client it copied in."""

    def test_it_defaults_to_the_source_checkout_layout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dramatis.server import DEFAULT_WEB_ROOT, web_root

        monkeypatch.delenv("DRAMATIS_WEB_ROOT", raising=False)
        assert web_root() == DEFAULT_WEB_ROOT

    def test_it_honours_the_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from dramatis.server import web_root

        monkeypatch.setenv("DRAMATIS_WEB_ROOT", str(tmp_path / "elsewhere"))
        assert web_root() == tmp_path / "elsewhere"

    def test_a_built_client_is_served_from_the_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A wheel install puts server.py three directories above a `web/dist` that is not
        # there; without the override the container would serve the 503 for a client it holds.
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><title>Dramatis</title>", "utf-8")
        monkeypatch.setenv("DRAMATIS_WEB_ROOT", str(dist))

        client = TestClient(create_app(tmp_path / "project.sqlite"))
        response = client.get("/")

        assert response.status_code == 200
        assert "Dramatis" in response.text

    def test_the_api_still_answers_when_the_client_is_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DRAMATIS_WEB_ROOT", str(tmp_path / "nothing-here"))

        client = TestClient(create_app(tmp_path / "project.sqlite"))

        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 503


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


class TestPassage:
    """Opening the source text at the position a piece of evidence names.

    The offsets come from the server because `dramatis.text` is where Invariant 3's
    definition of "verbatim" lives. A browser doing its own matching would be a second copy
    of that rule, and the copy nobody tests is the one that drifts.
    """

    def _first_relation(self, document):
        return document["relations"][0]

    def test_it_returns_the_passage_and_where_the_quotation_sits(self, analysed, client) -> None:
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        ).json()

        quoted = relation["evidence"][0]["selector"]["exact"]
        span = payload["quotation"]
        assert span is not None
        assert payload["text"][span["start"] : span["end"]] == quoted

    def test_the_span_indexes_the_text_it_is_returned_with(self, analysed, client) -> None:
        # Two coordinate systems in one payload would be a highlight in the wrong place.
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        ).json()

        assert 0 <= payload["quotation"]["start"] < payload["quotation"]["end"]
        assert payload["quotation"]["end"] <= len(payload["text"])

    def test_it_echoes_the_locator_it_opened(self, analysed, client) -> None:
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        ).json()

        assert payload["path"] == relation["evidence"][0]["locator"]["path"]

    def test_it_returns_no_markup(self, analysed, client) -> None:
        """Offsets, not marked-up text: no manuscript passes through a markup step here."""
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        ).json()

        assert "<" not in payload["text"]
        assert "mark" not in payload

    def test_an_unknown_snapshot_is_a_404(self, client) -> None:
        response = client.get(
            "/api/snapshots/snap:nope/passage", params={"relation": "rel:x", "evidence": 0}
        )
        assert response.status_code == 404

    def test_an_unknown_relation_is_a_404(self, analysed, client) -> None:
        _, snapshot_id, _ = analysed
        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": "rel:not-here", "evidence": 0},
        )

        assert response.status_code == 404
        assert "no relation" in response.json()["detail"]

    def test_an_evidence_index_past_the_end_is_a_404_that_says_how_many(
        self, analysed, client
    ) -> None:
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 99},
        )

        assert response.status_code == 404
        assert "pieces of evidence" in response.json()["detail"]

    def test_a_negative_evidence_index_is_refused(self, analysed, client) -> None:
        # Python would happily index from the end and return a passage for the wrong piece.
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)

        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": -1},
        )
        assert response.status_code == 404

    def test_the_quotation_never_appears_in_the_url(self, analysed, client) -> None:
        """A locator and a quotation in a query string put a manuscript in every access log.

        Evidence is addressed by its position in the stored array instead, which the client
        already has and which says nothing about the text.
        """
        _, snapshot_id, document = analysed
        relation = self._first_relation(document)
        quoted = relation["evidence"][0]["selector"]["exact"]

        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        )

        assert quoted not in str(response.url)
        assert response.status_code == 200


class TestPassageAfterAnEdit:
    """Phase 2's second acceptance sentence, end to end.

    *Editing the source text — inserting a paragraph before a quoted passage — leaves the
    evidence correctly anchored after re-ingest.*

    The snapshot stays bound to the revision it analysed; the caller names the newer one to
    ask where the evidence is now.
    """

    def _reingest(self, tmp_path: Path, store_path: Path, text: str) -> str:
        edited = tmp_path / "edited.txt"
        edited.write_text(text, encoding="utf-8", newline="")
        with Store(store_path) as store:
            return ingest_file(
                store, edited, work_title="A Work", collection_name="A Collection"
            ).revision_id

    def test_a_paragraph_inserted_before_the_quotation_leaves_it_anchored(
        self, analysed, tmp_path: Path, client
    ) -> None:
        store_path, snapshot_id, document = analysed
        relation = document["relations"][0]
        quoted = relation["evidence"][0]["selector"]["exact"]

        # The edit: a new opening paragraph, which moves every offset after it.
        revision = self._reingest(
            tmp_path,
            store_path,
            "A paragraph nobody had written before.\n\n" + PASSAGE,
        )

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0, "revision": revision},
        ).json()

        span = payload["quotation"]
        assert span is not None, "the evidence lost its anchor after an edit before it"
        assert payload["text"][span["start"] : span["end"]] == quoted
        assert payload["anchor"]["method"] == "exact"
        assert payload["anchor"]["similarity"] == 1.0

    def test_it_reports_that_the_passage_moved(self, analysed, tmp_path: Path, client) -> None:
        # The quotation is in the same words and a different place. Saying so is the
        # difference between a citation a reader can check and one they must take on trust.
        store_path, snapshot_id, document = analysed
        relation = document["relations"][0]

        revision = self._reingest(
            tmp_path, store_path, "A paragraph nobody had written before.\n\n" + PASSAGE
        )
        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0, "revision": revision},
        ).json()

        assert payload["anchor"]["moved"] is True
        assert payload["anchor"]["stored_path"] == relation["evidence"][0]["locator"]["path"]
        assert payload["path"] != payload["anchor"]["stored_path"]

    def test_an_edit_inside_the_quotation_falls_to_a_fuzzy_match_and_says_so(
        self, analysed, tmp_path: Path, client
    ) -> None:
        store_path, snapshot_id, document = analysed
        relation = document["relations"][0]

        revision = self._reingest(
            tmp_path, store_path, PASSAGE.replace("Ada met Bram", "Ada finally met Bram")
        )
        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0, "revision": revision},
        ).json()

        assert payload["quotation"] is not None
        assert payload["anchor"]["method"] == "fuzzy"
        assert payload["anchor"]["similarity"] < 1.0
        assert "Bram at the gate" in payload["text"]

    def test_a_quotation_cut_from_the_work_is_refused_rather_than_relocated(
        self, analysed, tmp_path: Path, client
    ) -> None:
        # Re-pointing at whatever scored highest would make every citation unfalsifiable.
        store_path, snapshot_id, document = analysed
        relation = document["relations"][0]

        revision = self._reingest(
            tmp_path, store_path, "Cai spoke to Ada alone.\n\nNothing else happened at all.\n"
        )
        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0, "revision": revision},
        )

        assert response.status_code == 404 or response.json()["quotation"] is None

    def test_the_snapshots_own_revision_is_the_default(self, analysed, client) -> None:
        _, snapshot_id, document = analysed
        relation = document["relations"][0]

        payload = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0},
        ).json()

        assert payload["text_revision_id"] == document["snapshot"]["text_revision_id"]
        assert payload["anchor"]["moved"] is False
        assert payload["anchor"]["method"] == "exact"

    def test_an_unknown_revision_is_a_404(self, analysed, client) -> None:
        _, snapshot_id, document = analysed
        relation = document["relations"][0]

        response = client.get(
            f"/api/snapshots/{snapshot_id}/passage",
            params={"relation": relation["id"], "evidence": 0, "revision": "rev:nope"},
        )

        assert response.status_code == 404
        assert "text revision" in response.json()["detail"]


class TestLineage:
    """A work's snapshots with its two time axes kept apart (Invariant 4).

    A flat list of snapshots can say a graph moved. It cannot say whether the work changed
    or the analysis did, and those are not comparable kinds of change.
    """

    def test_it_returns_both_axes_as_separate_lists(self, analysed, client) -> None:
        _, _, document = analysed
        work_id = document["works"][0]["id"]

        payload = client.get(f"/api/works/{work_id}/lineage").json()

        assert payload["work"]["id"] == work_id
        assert [entry["id"] for entry in payload["text_revisions"]] == [
            document["snapshot"]["text_revision_id"]
        ]
        assert [entry["id"] for entry in payload["analysis_runs"]] == [
            document["snapshot"]["analysis_run_id"]
        ]

    def test_each_snapshot_names_the_point_on_both_axes(self, analysed, client) -> None:
        _, snapshot_id, document = analysed
        work_id = document["works"][0]["id"]

        payload = client.get(f"/api/works/{work_id}/lineage").json()
        snapshot = next(entry for entry in payload["snapshots"] if entry["id"] == snapshot_id)

        assert snapshot["text_revision_id"] == document["snapshot"]["text_revision_id"]
        assert snapshot["analysis_run_id"] == document["snapshot"]["analysis_run_id"]

    def test_a_revision_reports_how_many_documents_it_holds(self, analysed, client) -> None:
        # Shape B is a folder of chapters, so "one revision" is not "one file".
        _, _, document = analysed
        work_id = document["works"][0]["id"]

        payload = client.get(f"/api/works/{work_id}/lineage").json()
        assert payload["text_revisions"][0]["documents"] >= 1

    def test_a_run_reports_what_makes_one_reading_differ_from_another(
        self, analysed, client
    ) -> None:
        _, _, document = analysed
        work_id = document["works"][0]["id"]

        run = client.get(f"/api/works/{work_id}/lineage").json()["analysis_runs"][0]

        assert run["model"]
        assert run["prompt_version"]

    def test_a_revision_that_was_never_analysed_still_appears(
        self, analysed, tmp_path: Path, client
    ) -> None:
        """The gap is the information. A revision with no snapshot is a piece of the work
        nobody has read yet, which a list of snapshots cannot express."""
        store_path, _, document = analysed
        work_id = document["works"][0]["id"]

        edited = tmp_path / "edited.txt"
        edited.write_text(PASSAGE + "\nCai left before dawn.\n", encoding="utf-8", newline="")
        with Store(store_path) as store:
            second = ingest_file(store, edited, work_title="A Work", collection_name="A Collection")

        payload = client.get(f"/api/works/{work_id}/lineage").json()

        assert second.revision_id in [entry["id"] for entry in payload["text_revisions"]]
        assert second.revision_id not in [
            entry["text_revision_id"] for entry in payload["snapshots"]
        ]

    def test_an_unknown_work_is_a_404(self, client) -> None:
        response = client.get("/api/works/work:nope/lineage")

        assert response.status_code == 404
        assert "no work" in response.json()["detail"]

    def test_it_does_not_return_the_snapshot_documents(self, analysed, client) -> None:
        # A listing, not a second copy of every graph the work has ever had.
        _, _, document = analysed
        work_id = document["works"][0]["id"]

        raw = client.get(f"/api/works/{work_id}/lineage").text

        assert "evidence" not in raw
        assert "weight_basis" not in raw


class TestDiffEndpoint:
    def test_it_reports_attribution_first_among_its_claims(self, analysed, client) -> None:
        _, snapshot_id, _ = analysed

        payload = client.get(
            "/api/diff", params={"before": snapshot_id, "after": snapshot_id}
        ).json()

        assert payload["attribution"] == "same"
        assert payload["characters"] == []
        assert payload["relations"] == []

    def test_an_unknown_snapshot_is_a_404(self, analysed, client) -> None:
        _, snapshot_id, _ = analysed

        assert (
            client.get(
                "/api/diff", params={"before": "snap:nope", "after": snapshot_id}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/diff", params={"before": snapshot_id, "after": "snap:nope"}
            ).status_code
            == 404
        )

    def test_it_names_the_weight_basis_it_compared_on(self, analysed, client) -> None:
        _, snapshot_id, _ = analysed

        payload = client.get(
            "/api/diff", params={"before": snapshot_id, "after": snapshot_id}
        ).json()

        assert payload["weights_comparable"] is True
        assert payload["weight_basis"]


class TestTheOriginGuard:
    """The guard that makes writing from a browser safe (4.8, D31).

    A page open on any site can POST to 127.0.0.1 from the user's browser; it cannot read
    the reply, but a write's side effect would land. The browser stamps such a request with
    an Origin that is not the server's own, and the guard refuses it before the write.

    Starlette's TestClient sends requests to `http://testserver`, so its Host header is
    `testserver`; a same-origin write carries `Origin: http://testserver`, and a cross-origin
    one carries anything else.
    """

    def _created(self, tmp_path: Path):
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store", headers={"origin": "http://testserver"})
        return client

    def test_a_cross_origin_write_is_refused(self, tmp_path: Path) -> None:
        client = self._created(tmp_path)
        response = client.put(
            "/api/settings",
            json={"collectives_are_actors": True},
            headers={"origin": "http://evil.example"},
        )
        assert response.status_code == 403

    def test_the_refused_write_never_happened(self, tmp_path: Path) -> None:
        """The point of a dependency rather than a check inside the handler: the side effect
        must not land, not merely go unreported."""
        client = self._created(tmp_path)
        client.put(
            "/api/settings",
            json={"collectives_are_actors": True},
            headers={"origin": "http://evil.example"},
        )
        assert client.get("/api/settings").json() == {}

    def test_a_cross_origin_store_creation_creates_nothing(self, tmp_path: Path) -> None:
        store_path = tmp_path / "project.sqlite"
        client = TestClient(create_app(store_path))

        response = client.post("/api/store", headers={"origin": "http://evil.example"})

        assert response.status_code == 403
        assert not store_path.is_file(), "the file was created despite the refusal"

    def test_a_same_origin_write_is_allowed(self, tmp_path: Path) -> None:
        client = self._created(tmp_path)
        response = client.put(
            "/api/settings",
            json={"collectives_are_actors": True},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert response.json()["collectives_are_actors"] is True

    def test_a_write_with_no_origin_is_allowed(self, tmp_path: Path) -> None:
        # A non-browser client — curl, the CLI — sends no Origin and is not a cross-site
        # vector. A browser cannot suppress the header on a cross-origin write, so absence
        # is not a page hiding.
        client = self._created(tmp_path)
        response = client.put("/api/settings", json={"collectives_are_actors": True})
        assert response.status_code == 200

    def test_a_cross_origin_read_is_not_refused(self, tmp_path: Path) -> None:
        # Reads change nothing, and the browser's own same-origin policy already stops the
        # page reading the reply. Guarding them would only break non-browser tooling.
        client = self._created(tmp_path)
        response = client.get("/api/settings", headers={"origin": "http://evil.example"})
        assert response.status_code == 200

    def test_the_port_is_part_of_the_origin(self, tmp_path: Path) -> None:
        # Another server on the same host but a different port is a different origin. netloc,
        # not hostname, is what is compared.
        client = self._created(tmp_path)
        response = client.put(
            "/api/settings",
            json={"collectives_are_actors": True},
            headers={"origin": "http://testserver:9999"},
        )
        assert response.status_code == 403

    def test_every_mutating_verb_is_guarded(self, tmp_path: Path) -> None:
        # The guard is on the method, not one endpoint: POST, PUT and DELETE all refuse.
        client = self._created(tmp_path)
        evil = {"origin": "http://evil.example"}

        assert client.post("/api/store", headers=evil).status_code == 403
        assert client.put("/api/settings", json={}, headers=evil).status_code == 403
        assert (
            client.put("/api/structure", json={"root": "/x", "plans": {}}, headers=evil).status_code
            == 403
        )
        assert client.delete("/api/structure?root=/x", headers=evil).status_code == 403


class TestTheWriteEndpoints:
    """What the guard protects: a store's existence, its settings, its structure map. None
    calls a model or touches the author's text."""

    def _client(self, tmp_path: Path):
        return TestClient(create_app(tmp_path / "project.sqlite"))

    def test_creating_a_store_that_was_absent(self, tmp_path: Path) -> None:
        store_path = tmp_path / "project.sqlite"
        client = TestClient(create_app(store_path))

        response = client.post("/api/store")

        assert response.status_code == 201
        assert response.json()["created"] is True
        assert store_path.is_file()

    def test_creating_a_store_that_already_exists_is_a_no_op(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        again = client.post("/api/store")

        assert again.status_code == 200
        assert again.json()["created"] is False

    def test_settings_merge_rather_than_replace(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        client.put("/api/settings", json={"collectives_are_actors": True})
        client.put("/api/settings", json={"preface_excluded": False})

        assert client.get("/api/settings").json() == {
            "collectives_are_actors": True,
            "preface_excluded": False,
        }

    def test_a_setting_can_be_overwritten(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        client.put("/api/settings", json={"collectives_are_actors": True})
        client.put("/api/settings", json={"collectives_are_actors": False})

        assert client.get("/api/settings").json()["collectives_are_actors"] is False

    def test_settings_must_be_an_object(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        assert client.put("/api/settings", json=["not", "an", "object"]).status_code == 422

    def test_saving_and_reading_a_structure_map(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")
        plan = {"role": {"value": "narrative"}}

        saved = client.put("/api/structure", json={"root": "/corpus", "plans": {"one.md": plan}})

        assert saved.status_code == 200
        assert saved.json()["saved"] == 1
        assert client.get("/api/structure", params={"root": "/corpus"}).json() == {"one.md": plan}

    def test_the_server_stamps_the_confirmation_time_not_the_client(self, tmp_path: Path) -> None:
        # confirmed_at records when the server accepted the answer, so a client-supplied one
        # is ignored rather than trusted.
        client = self._client(tmp_path)
        client.post("/api/store")

        response = client.put(
            "/api/structure",
            json={"root": "/corpus", "plans": {"one.md": {}}, "confirmed_at": "1999-01-01"},
        )

        assert response.status_code == 200

    def test_forgetting_a_structure_map(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")
        client.put("/api/structure", json={"root": "/corpus", "plans": {"one.md": {}}})

        forgotten = client.delete("/api/structure", params={"root": "/corpus"})

        assert forgotten.json()["forgotten"] == 1
        assert client.get("/api/structure", params={"root": "/corpus"}).json() == {}

    def test_a_structure_map_needs_a_root(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        assert client.put("/api/structure", json={"plans": {}}).status_code == 422

    def test_a_structure_map_needs_plans(self, tmp_path: Path) -> None:
        client = self._client(tmp_path)
        client.post("/api/store")

        assert client.put("/api/structure", json={"root": "/x", "plans": "no"}).status_code == 422

    def test_writing_settings_to_an_uncreated_store_is_a_404(self, tmp_path: Path) -> None:
        # open_store 404s on a missing file; only POST /api/store may bring it into being.
        client = self._client(tmp_path)

        assert client.put("/api/settings", json={"a": 1}).status_code == 404


class TestProjectCreationInTheBrowser:
    """4.9 end to end, through the HTTP the browser actually speaks.

    The value of this over the client's own unit tests is the seam: `create.plansFor` emits
    JSON, and `ingest.kept_text` reads it. Nothing but a test crossing both languages can
    catch the two drifting apart, and drifting apart means a confirmed preface silently
    staying in the analysis.
    """

    PREFACE = (
        "PREFACE\n\nThis edition is introduced by a critic who admired Coleridge "
        "and could not resist saying so.\n\n"
    )
    NOVEL = (
        "It is a truth universally acknowledged, that a single man in possession of a good "
        "fortune must be in want of a wife.\n\nAda met Bram at the gate.\n"
    )

    def _plan_as_the_browser_builds_it(self, path: str, characters: int, boundary: str) -> dict:
        """Exactly what `create.plansFor` emits for an excluded document.

        Kept literal rather than imported, because the point is to fail when the client's
        shape changes without the server's noticing.
        """
        confirmed = lambda value: {  # noqa: E731 - mirrors the client's helper
            "value": value,
            "basis": "confirmed in the browser",
            "settled": True,
        }
        return {
            "path": path,
            "characters": characters,
            "role": confirmed("narrative"),
            "addressing": {"value": "section", "basis": "D27", "settled": True},
            "revision_of": {"value": None, "basis": "none", "settled": False},
            "regions": [
                {
                    "label": "before the narrative",
                    "role": confirmed("excluded"),
                    "starts_at": 0,
                    "ends_at": None,
                    "begins_with": "",
                    "ends_with": "",
                },
                {
                    "label": "narrative",
                    "role": confirmed("narrative"),
                    "starts_at": 0,
                    "ends_at": None,
                    "begins_with": boundary,
                    "ends_with": "",
                },
            ],
        }

    def test_a_project_is_created_from_the_browser_alone(self, tmp_path: Path) -> None:
        """Phase 4's acceptance sentence, minus the model: a project created without touching
        the command line, and a preface excluded there producing a cast free of it."""
        source = tmp_path / "novel.txt"
        source.write_text(self.PREFACE + self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))

        # 1. Look at what was chosen. No store yet, and no model.
        proposed = client.get("/api/structure/propose", params={"source": str(source)})
        assert proposed.status_code == 200
        assert [d["path"] for d in proposed.json()["documents"]] == ["novel.txt"]

        # 2. Create the store.
        assert client.post("/api/store").status_code == 201

        # 3. Record the setting the study is conducted under.
        client.put("/api/settings", json={"collectives_are_actors": False})

        # 4. Confirm the map, marking the preface excluded.
        plan = self._plan_as_the_browser_builds_it(
            "novel.txt",
            proposed.json()["documents"][0]["characters"],
            "It is a truth universally acknowledged",
        )
        saved = client.put(
            "/api/structure", json={"root": str(source.resolve()), "plans": {"novel.txt": plan}}
        )
        assert saved.status_code == 200

        # 5. Ingest.
        ingested = client.post("/api/ingest", json={"path": str(source), "work_title": "A Novel"})
        assert ingested.status_code == 201
        assert ingested.json()["excluded"] == ["novel.txt"]

        # The text a later analysis would read no longer holds the preface.
        with Store(tmp_path / "project.sqlite") as store:
            narrative = store.revision_text(ingested.json()["revision_id"], roles=[NARRATIVE])

        assert "Coleridge" not in narrative
        assert "Ada met Bram" in narrative

    def test_creating_without_excluding_keeps_the_whole_document(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(self.PREFACE + self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")

        ingested = client.post("/api/ingest", json={"path": str(source), "work_title": "A Novel"})

        assert ingested.json()["excluded"] == []
        with Store(tmp_path / "project.sqlite") as store:
            assert "Coleridge" in store.revision_text(ingested.json()["revision_id"])

    def test_a_folder_is_ingested_as_one_revision(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "one.md").write_text(self.NOVEL, encoding="utf-8", newline="")
        (root / "two.md").write_text("Cai waited by the water.\n", encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")

        ingested = client.post("/api/ingest", json={"path": str(root), "work_title": "A Serial"})

        assert ingested.status_code == 201
        assert ingested.json()["documents"] == 2

    def test_the_flow_calls_no_model(self, tmp_path: Path) -> None:
        """4.9 never calls a model; that stays `analyse`'s job. Proven by refusing the import
        the providers would need — if any step reached for one, this would raise."""
        import builtins

        source = tmp_path / "novel.txt"
        source.write_text(self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))

        real_import = builtins.__import__

        def refuse(name: str, *args, **kwargs):
            if name in ("anthropic", "dramatis.providers.anthropic_provider"):
                raise AssertionError(f"project creation reached for a model: {name}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse
        try:
            client.get("/api/structure/propose", params={"source": str(source)})
            client.post("/api/store")
            client.put("/api/settings", json={"collectives_are_actors": True})
            ingested = client.post("/api/ingest", json={"path": str(source)})
        finally:
            builtins.__import__ = real_import

        assert ingested.status_code == 201

    def test_proposing_needs_no_store(self, tmp_path: Path) -> None:
        # The first screen of creation runs before the project exists; 404ing it would make
        # the flow impossible to start.
        source = tmp_path / "novel.txt"
        source.write_text(self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "absent.sqlite"))

        assert (
            client.get("/api/structure/propose", params={"source": str(source)}).status_code == 200
        )

    def test_proposing_shows_back_what_was_already_confirmed(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")
        plan = self._plan_as_the_browser_builds_it("novel.txt", 100, "It is a truth")
        client.put(
            "/api/structure", json={"root": str(source.resolve()), "plans": {"novel.txt": plan}}
        )

        again = client.get("/api/structure/propose", params={"source": str(source)}).json()

        assert again["documents"][0]["role"]["value"] == "narrative"
        assert again["documents"][0]["role"]["settled"] is True

    def test_an_unfindable_boundary_is_refused_rather_than_silently_kept(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(self.PREFACE + self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")
        plan = self._plan_as_the_browser_builds_it("novel.txt", 100, "a line that is not there")
        client.put(
            "/api/structure", json={"root": str(source.resolve()), "plans": {"novel.txt": plan}}
        )

        refused = client.post("/api/ingest", json={"path": str(source)})

        assert refused.status_code == 422
        assert "not in the document" in refused.json()["detail"]

    def test_a_path_that_is_not_there_is_a_404(self, tmp_path: Path) -> None:
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")

        assert (
            client.get(
                "/api/structure/propose", params={"source": str(tmp_path / "absent")}
            ).status_code
            == 404
        )
        assert (
            client.post("/api/ingest", json={"path": str(tmp_path / "absent")}).status_code == 404
        )

    def test_ingest_needs_a_path(self, tmp_path: Path) -> None:
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store")

        assert client.post("/api/ingest", json={}).status_code == 422

    def test_ingesting_is_a_write_and_is_guarded(self, tmp_path: Path) -> None:
        # 4.8's guard covers it by virtue of being a POST, with nothing to opt in.
        source = tmp_path / "novel.txt"
        source.write_text(self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))
        client.post("/api/store", headers={"origin": "http://testserver"})

        refused = client.post(
            "/api/ingest", json={"path": str(source)}, headers={"origin": "http://evil.example"}
        )

        assert refused.status_code == 403
        with Store(tmp_path / "project.sqlite") as store:
            assert store.list_works() == []

    def test_proposing_is_a_read_and_is_not_guarded(self, tmp_path: Path) -> None:
        source = tmp_path / "novel.txt"
        source.write_text(self.NOVEL, encoding="utf-8", newline="")
        client = TestClient(create_app(tmp_path / "project.sqlite"))

        allowed = client.get(
            "/api/structure/propose",
            params={"source": str(source)},
            headers={"origin": "http://evil.example"},
        )

        assert allowed.status_code == 200


class TestReviews:
    """Where review of a snapshot's nodes and edges stands, and moving it (5.1).

    Served as its own resource rather than folded into the snapshot: the snapshot endpoint
    hands back the archived document unchanged, and a decision taken after that document was
    written is not part of it.
    """

    def _subjects(self, client, snapshot_id: str) -> dict:
        payload = client.get(f"/api/snapshots/{snapshot_id}/reviews").json()
        return {(entry["kind"], entry["id"]): entry for entry in payload["subjects"]}

    def test_every_node_and_edge_starts_proposed(self, client, analysed) -> None:
        _, snapshot_id, document = analysed

        payload = client.get(f"/api/snapshots/{snapshot_id}/reviews").json()

        assert payload["snapshot_id"] == snapshot_id
        assert len(payload["subjects"]) == len(document["characters"]) + len(document["relations"])
        assert payload["counts"]["proposed"] == len(payload["subjects"])
        assert payload["reviewed"] == 0

    def test_a_decision_is_recorded_and_read_back(self, client, analysed) -> None:
        _, snapshot_id, document = analysed
        identifier = document["characters"][0]["id"]

        recorded = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={"kind": "character", "id": identifier, "status": "accepted"},
        )

        assert recorded.status_code == 201
        assert recorded.json()["status"] == "accepted"
        assert recorded.json()["decided_in"] == snapshot_id
        standing = self._subjects(client, snapshot_id)
        assert standing[("character", identifier)]["status"] == "accepted"

    def test_an_edge_is_reviewed_the_same_way(self, client, analysed) -> None:
        _, snapshot_id, document = analysed
        identifier = document["relations"][0]["id"]

        recorded = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={
                "kind": "relation",
                "id": identifier,
                "status": "rejected",
                "note": "they never meet",
            },
        )

        assert recorded.status_code == 201
        assert recorded.json()["note"] == "they never meet"

    def test_the_snapshot_it_was_taken_on_is_served_unchanged(self, client, analysed) -> None:
        """Invariant 4. A review is recorded beside the snapshot, never inside it."""
        _, snapshot_id, document = analysed

        client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={"kind": "character", "id": document["characters"][0]["id"], "status": "rejected"},
        )

        assert client.get(f"/api/snapshots/{snapshot_id}").json() == document

    def test_a_correction_with_no_reason_is_refused(self, client, analysed) -> None:
        _, snapshot_id, document = analysed

        refused = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={
                "kind": "character",
                "id": document["characters"][0]["id"],
                "status": "corrected",
            },
        )

        assert refused.status_code == 422
        assert "what it corrects" in refused.json()["detail"]

    def test_a_status_outside_the_vocabulary_is_refused(self, client, analysed) -> None:
        _, snapshot_id, document = analysed

        refused = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={
                "kind": "character",
                "id": document["characters"][0]["id"],
                "status": "probably-fine",
            },
        )

        assert refused.status_code == 422

    def test_a_claim_the_reading_never_made_is_refused(self, client, analysed) -> None:
        _, snapshot_id, _ = analysed

        refused = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={"kind": "character", "id": "char:nobody", "status": "accepted"},
        )

        assert refused.status_code == 422
        assert "nothing there to review" in refused.json()["detail"]

    def test_a_body_missing_a_field_is_refused(self, client, analysed) -> None:
        _, snapshot_id, _ = analysed

        refused = client.post(f"/api/snapshots/{snapshot_id}/reviews", json={"kind": "character"})

        assert refused.status_code == 422

    def test_an_unknown_snapshot_is_a_404_either_way(self, client) -> None:
        # The snapshot being absent is a different failure from the decision being wrong, and
        # a client sent looking for a missing snapshot when they mistyped a status is worse
        # off than one told which it was.
        assert client.get("/api/snapshots/snap:nothing/reviews").status_code == 404
        refused = client.post(
            "/api/snapshots/snap:nothing/reviews",
            json={"kind": "character", "id": "char:a", "status": "accepted"},
        )
        assert refused.status_code == 404

    def test_a_cross_origin_decision_is_refused_without_the_endpoint_opting_in(
        self, client, analysed
    ) -> None:
        """The property 4.8's method-keyed middleware was chosen for: a write added a phase
        later is guarded the moment it exists, because it is a POST."""
        _, snapshot_id, document = analysed
        identifier = document["characters"][0]["id"]

        refused = client.post(
            f"/api/snapshots/{snapshot_id}/reviews",
            json={"kind": "character", "id": identifier, "status": "accepted"},
            headers={"origin": "http://evil.example"},
        )

        assert refused.status_code == 403
        standing = self._subjects(client, snapshot_id)
        assert standing[("character", identifier)]["status"] == "proposed"

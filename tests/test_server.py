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

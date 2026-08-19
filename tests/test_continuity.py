"""The continuity report (5.4).

Three findings, each of which re-analysing cannot produce. A cast with a stale name in it
looks exactly like a cast; a locator pointing at a deleted section fails one citation at a
time; two copies of a chapter are an ordinary corpus to a reader that was not told they are
the same chapter.

The acceptance sentence is tested against fixture **C** directly: *renaming an entity across
one document while leaving stale references in another produces a continuity report naming
every stale location*.

Nothing here calls a model. The report is arithmetic over two texts the store already holds,
so the snapshot is produced with a scripted provider and everything after it is offline.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dramatis import continuity
from dramatis.ingest import ingest_file, ingest_folder
from dramatis.pipeline import analyse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Store

FIXTURE_C = Path(__file__).resolve().parents[1] / "fixtures" / "c"


def reading(
    names: tuple[str, ...] = ("Ada", "Yeong"),
    interactions: list[dict] | None = None,
) -> ScriptedProvider:
    """A provider that answers every call with a reply valid for whichever stage asked.

    One object carrying `characters`, `interactions` and `groups`: extraction reads the first
    two and resolution the third. A scripted *list* would have to know how many windows the
    corpus divides into, and a test that fails because a fixture grew by a paragraph is a test
    about window sizing rather than about what it claims to check.
    """
    payload = json.dumps(
        {
            "characters": [{"name": n, "aliases": [], "kind": "person"} for n in names],
            "interactions": interactions or [],
            "groups": [
                {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                for n in names
            ],
        }
    )
    return ScriptedProvider(lambda _request: payload)


@pytest.fixture
def corpus(tmp_path: Path):
    """Fixture C copied somewhere writable, so a revision can be made of it.

    The fixture's own README and manifest are left out. They describe the corpus rather than
    belonging to it, and the README names the characters — so ingesting it would put a third
    document into the work whose mentions are documentation. That the report *did* find them
    is the check working; it is the corpus that is wrong here, not the finding.
    """
    root = tmp_path / "corpus"
    shutil.copytree(FIXTURE_C, root)
    for describing in ("README.md", "corpus.json"):
        (root / describing).unlink(missing_ok=True)
    return root


@pytest.fixture
def analysed(tmp_path: Path, corpus: Path):
    """Fixture C ingested and read once, with the store left open."""
    store_path = tmp_path / "project.sqlite"
    with Store(store_path) as store:
        ingested = ingest_folder(store, corpus, work_title="Transmissions", collection_name="C")
        result = analyse(
            store,
            ingested.revision_id,
            reading(),
            now="2026-01-01T09:00:00+00:00",
        )
        yield store, result.snapshot, corpus


def rename_in(root: Path, relative: str, before: str, after: str) -> None:
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert before in text, f"{relative} does not mention {before!r}"
    path.write_text(text.replace(before, after), encoding="utf-8", newline="")


def reingest(store: Store, root: Path) -> str:
    ingested = ingest_folder(store, root, work_title="Transmissions", collection_name="C")
    return ingested.revision_id


class TestNothingHasMoved:
    def test_a_reading_of_the_current_text_reports_nothing(self, analysed) -> None:
        store, snapshot, _ = analysed

        found = continuity.report(store, snapshot.work_id)

        assert found.unchanged
        assert found.empty
        assert len(found) == 0

    def test_an_unknown_work_is_refused(self, analysed) -> None:
        store, _, _ = analysed
        with pytest.raises(continuity.ContinuityError, match="unknown work"):
            continuity.report(store, "work:nothing")

    def test_a_snapshot_of_another_work_is_refused(self, analysed) -> None:
        store, snapshot, _ = analysed
        with pytest.raises(continuity.ContinuityError, match="no snapshot"):
            continuity.report(store, snapshot.work_id, snapshot_id="snap:nothing")


class TestTheAcceptanceSentence:
    """*Renaming an entity across one document of fixture C while leaving stale references in
    another produces a continuity report naming every stale location.*"""

    def _renamed(self, store: Store, corpus: Path) -> str:
        # Renamed through the narrative and not in the bible, which is the mistake: the
        # transmissions call her Sarto now and the character bible still says Yeong.
        for transmission in ("t01.md", "t02.md", "t03.md"):
            rename_in(corpus, f"transmissions/{transmission}", "Yeong", "Sarto")
        return reingest(store, corpus)

    def test_the_stale_name_is_found(self, analysed) -> None:
        store, snapshot, corpus = analysed
        # The bible must be where the staleness lands, so put the old name there first.
        bible = corpus / "series-bible" / "ada-mbeki.md"
        bible.write_text(
            bible.read_text(encoding="utf-8") + "\nSister Yeong keeps the low band.\n",
            encoding="utf-8",
            newline="",
        )
        revision = reingest(store, corpus)
        result = analyse(
            store,
            revision,
            reading(),
            now="2026-01-02T09:00:00+00:00",
        )

        after = self._renamed(store, corpus)
        found = continuity.report(store, result.snapshot.work_id, against=after)

        stale = [entry for entry in found.stale_names if entry.form == "Yeong"]
        assert stale, "the rename left 'Yeong' in the bible and it was not reported"

    def test_it_names_every_stale_location(self, analysed) -> None:
        store, snapshot, corpus = analysed
        bible = corpus / "series-bible" / "ada-mbeki.md"
        bible.write_text(
            bible.read_text(encoding="utf-8")
            + "\nSister Yeong keeps the low band. Yeong reports to Ada.\n",
            encoding="utf-8",
            newline="",
        )
        revision = reingest(store, corpus)
        result = analyse(store, revision, reading(), now="2026-01-02T09:00:00+00:00")

        after = self._renamed(store, corpus)
        found = continuity.report(store, result.snapshot.work_id, against=after)

        stale = next(entry for entry in found.stale_names if entry.form == "Yeong")
        # Every one, not a count: the point of the report is that fixing it is a list rather
        # than a search.
        assert len(stale.locations) == 2
        assert all("ada-mbeki" in (location.document_path or "") for location in stale.locations)
        assert all(location.exact == "Yeong" for location in stale.locations)
        assert any("low band" in location.suffix for location in stale.locations)

    def test_it_names_the_documents_the_rename_reached(self, analysed) -> None:
        store, snapshot, corpus = analysed
        bible = corpus / "series-bible" / "ada-mbeki.md"
        bible.write_text(
            bible.read_text(encoding="utf-8") + "\nSister Yeong keeps the low band.\n",
            encoding="utf-8",
            newline="",
        )
        revision = reingest(store, corpus)
        result = analyse(store, revision, reading(), now="2026-01-02T09:00:00+00:00")

        after = self._renamed(store, corpus)
        found = continuity.report(store, result.snapshot.work_id, against=after)

        stale = next(entry for entry in found.stale_names if entry.form == "Yeong")
        assert set(stale.retired_from) == {
            "transmissions/t01.md",
            "transmissions/t02.md",
            "transmissions/t03.md",
        }

    def test_a_name_removed_everywhere_is_not_a_stale_reference(self, analysed) -> None:
        """A clean removal leaves nothing behind. Reporting it would be reporting an edit
        rather than an inconsistency, and the report would cry wolf on every draft."""
        store, snapshot, corpus = analysed
        after = self._renamed(store, corpus)

        found = continuity.report(store, snapshot.work_id, against=after)

        assert not [entry for entry in found.stale_names if entry.form == "Yeong"]

    def test_a_name_present_throughout_is_never_a_candidate(self, analysed) -> None:
        """Why no stop-list is needed. A form only qualifies where it vanished from a
        document entirely, which no common word ever does."""
        store, snapshot, corpus = analysed
        self._renamed(store, corpus)
        after = reingest(store, corpus)

        found = continuity.report(store, snapshot.work_id, against=after)

        assert not [entry for entry in found.stale_names if entry.form == "Ada"]


class TestLostPositions:
    def test_evidence_pointing_at_a_deleted_position_is_reported(self, tmp_path: Path) -> None:
        """A locator names a place. Cut the text down and the place stops existing, which
        nothing else reports: `passage` fails one citation at a time, and only when somebody
        opens it."""
        source = tmp_path / "work.txt"
        source.write_text(
            "Ada met Bram at the gate.\n\nBram did not answer her.\n\n"
            "Cai spoke to Ada alone.\n\nAda walked home.\n",
            encoding="utf-8",
            newline="",
        )
        reply = json.dumps(
            {
                "characters": [
                    {"name": n, "aliases": [], "kind": "person"} for n in ("Ada", "Cai")
                ],
                "interactions": [
                    {
                        "participants": ["Ada", "Cai"],
                        "quotation": "Cai spoke to Ada alone.",
                        "note": "",
                    }
                ],
            }
        )
        grouping = json.dumps(
            {
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Ada", "Cai")
                ]
            }
        )

        with Store(tmp_path / "project.sqlite") as store:
            ingested = ingest_file(store, source, work_title="A Work", collection_name="A")
            result = analyse(
                store,
                ingested.revision_id,
                ScriptedProvider([reply, grouping]),
                now="2026-01-01T09:00:00+00:00",
            )
            evidenced = [
                piece
                for relation in result.snapshot.document["relations"]
                for piece in relation.get("evidence") or []
            ]
            assert evidenced, "the fixture must produce evidence for this to check anything"

            # The paragraph the evidence points at, and everything after it, cut away.
            source.write_text(
                "Ada met Bram at the gate.\n\nBram did not answer her.\n",
                encoding="utf-8",
                newline="",
            )
            shorter = ingest_file(
                store, source, work_title="A Work", collection_name="A"
            ).revision_id

            found = continuity.report(store, result.snapshot.work_id, against=shorter)

        assert found.lost_positions, "a position that no longer exists went unreported"
        lost = found.lost_positions[0]
        assert lost.subject_kind == "relation"
        assert lost.quotation == "Cai spoke to Ada alone."
        # The words went with the position, so this is a claim with nothing behind it rather
        # than a citation that moved.
        assert lost.words_survive is False


class TestSupersededDocuments:
    def test_a_revision_holding_both_drafts_of_a_chapter_is_reported(self, tmp_path: Path) -> None:
        """The structure map says one document revises another. A revision holding both reads
        that chapter twice and weighs every interaction in it double, and nothing else
        notices."""
        root = tmp_path / "corpus"
        (root / "draft-1").mkdir(parents=True)
        (root / "draft-2").mkdir(parents=True)
        (root / "draft-1" / "chapter-01.md").write_text(
            "Ada met Bram at the gate.\n", encoding="utf-8", newline=""
        )
        (root / "draft-2" / "chapter-01.md").write_text(
            "Ada met Bram at the gate, later than she meant to.\n",
            encoding="utf-8",
            newline="",
        )

        with Store(tmp_path / "project.sqlite") as store:
            store.save_structure_map(
                str(root),
                {
                    "draft-2/chapter-01.md": {
                        "role": {"value": "narrative"},
                        "revision_of": {"value": "draft-1/chapter-01.md"},
                    }
                },
                "2026-01-01T00:00:00+00:00",
            )
            ingested = ingest_folder(store, root, work_title="A Work", collection_name="A")
            result = analyse(
                store,
                ingested.revision_id,
                reading(("Ada", "Bram")),
                now="2026-01-01T09:00:00+00:00",
            )

            found = continuity.report(store, result.snapshot.work_id)

        assert [entry.path for entry in found.superseded] == ["draft-1/chapter-01.md"]
        assert found.superseded[0].superseded_by == "draft-2/chapter-01.md"
        # Reported even though nothing has been revised since the reading: a superseded
        # document read alongside its replacement is wrong in the revision that holds them.
        assert found.unchanged
        assert not found.empty

    def test_a_revision_holding_only_the_later_draft_is_clean(self, tmp_path: Path) -> None:
        root = tmp_path / "corpus"
        (root / "draft-2").mkdir(parents=True)
        (root / "draft-2" / "chapter-01.md").write_text(
            "Ada met Bram at the gate.\n", encoding="utf-8", newline=""
        )

        with Store(tmp_path / "project.sqlite") as store:
            store.save_structure_map(
                str(root),
                {
                    "draft-2/chapter-01.md": {
                        "role": {"value": "narrative"},
                        "revision_of": {"value": "draft-1/chapter-01.md"},
                    }
                },
                "2026-01-01T00:00:00+00:00",
            )
            ingested = ingest_folder(store, root, work_title="A Work", collection_name="A")
            result = analyse(
                store,
                ingested.revision_id,
                reading(("Ada", "Bram")),
                now="2026-01-01T09:00:00+00:00",
            )

            found = continuity.report(store, result.snapshot.work_id)

        assert found.superseded == ()


class TestAsJson:
    def test_the_document_carries_every_finding_and_the_axes_it_compared(self, analysed) -> None:
        store, snapshot, _ = analysed

        payload = continuity.as_json(continuity.report(store, snapshot.work_id))

        assert payload["snapshot_id"] == snapshot.id
        assert payload["read_revision"] == snapshot.text_revision_id
        assert payload["against_revision"] == snapshot.text_revision_id
        assert payload["unchanged"] is True
        assert payload["findings"] == 0
        for key in ("stale_names", "lost_positions", "superseded", "notes"):
            assert payload[key] == []


class TestReadingALocation:
    def test_the_space_beside_the_name_is_kept(self) -> None:
        """`normalise_whitespace` strips both ends, which is right for matching a quotation
        and wrong for showing one: it renders "Sister Yeong keeps" as "Sister[Yeong]keeps".
        Found by reading the report rather than by testing it."""
        text = "Sister Yeong keeps the low band."
        at = text.index("Yeong")

        location = continuity._location("doc:1", "bible.md", text, at, "Yeong")

        assert location.prefix.endswith(" ")
        assert location.suffix.startswith(" ")
        assert str(location).endswith("...Sister [Yeong] keeps the low band....")

    def test_context_cut_mid_word_keeps_no_space(self) -> None:
        # The window is a fixed number of characters, so it often lands inside a word. A
        # space invented there would misrepresent the text.
        assert continuity._context("the archivist", keep="end") == "the archivist"
        assert continuity._context("the archivist ", keep="end") == "the archivist "
        assert continuity._context("ist walked", keep="start") == "ist walked"
        assert continuity._context(" walked on", keep="start") == " walked on"

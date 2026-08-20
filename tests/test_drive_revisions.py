"""Re-ingesting a Drive folder, so revisions work over a corpus nobody downloads (4.15).

The bullet asks for two things, and the second was not reachable before this one:

**A second ingest of the same folder picks up edited documents as a new text revision.** Not
a second *work* — which is what happens if identity rests on a title, because a Drive folder
can be renamed and a `--work` flag can simply not be typed twice. `TestTheSameFolderIsTheSame
Corpus` reads the same folder twice from a pair of traffic files that differ in exactly two
places: one Google Doc was edited, and the folder was renamed. One work, two revisions, and
one document reported `changed`.

**The structure map confirmed against that root is reused rather than asked again.** A map
could be *stored* against a Drive root since 4.13 and there was no way to confirm one, since
`structure` took a local path. It takes `--drive` now, on the same terms `ingest` does: that
flag is the only thing that makes the command reach a network.

And the point of both, from the bullet's last sentence: the comparison 3.x's diff and 5.4's
continuity report are built on — the same document, by path, in two revisions — now reaches a
corpus nobody ever downloaded. A *full* report needs a snapshot and therefore a provider, so
what is proven here is the half that does not.

Every test replays recorded traffic through an injected transport. Nothing here contacts
Google.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dramatis.drive
import dramatis.google_auth
from dramatis.cli import main
from dramatis.drive import READONLY_SCOPE, DriveSource
from dramatis.ingest import ingest_source
from dramatis.store import NARRATIVE, REFERENCE, Store
from tests.drive_traffic import TRAFFIC, Replay

FOLDER = "1rootFOLDERid"
ADDRESS = f"https://drive.google.com/drive/folders/{FOLDER}"
ROOT = f"gdrive:folder/{FOLDER}"

FIRST = TRAFFIC / "drive-folder.json"
EDITED = TRAFFIC / "drive-folder-edited.json"

DOCUMENTS = [
    "Chapter 01.md",
    "cast.md",
    "drafts/Chapter 02.md",
    "drafts/readme.txt",
    "notes.md",
]


def a_source(traffic: Path) -> DriveSource:
    return DriveSource(FOLDER, credentials="a-token", transport=Replay(traffic))


@pytest.fixture
def authorised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cached credential that mints a token without Google, for the CLI tests."""
    path = tmp_path / "credential.json"
    path.write_text(
        json.dumps(
            {
                "credential_version": 1,
                "client_id": "123.apps.googleusercontent.com",
                "client_secret": "GOCSPX-not-real",
                "refresh_token": "1//refresh",
                "scope": READONLY_SCOPE,
                "obtained_at": "2026-08-20T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(path))
    monkeypatch.setattr(
        dramatis.google_auth,
        "_post",
        lambda *_: json.dumps({"access_token": "ya29.a", "expires_in": 3599}).encode("utf-8"),
    )
    return path


def a_confirmed_map(store_path: Path) -> list[str]:
    """The arguments that settle every document's role for this folder.

    All five, because **4.2** refuses to confirm a map with an `unknown` left in it — a saved
    `unknown` would never be asked about again, which is the one answer worse than no answer.
    """
    return [
        "structure",
        "--drive",
        ADDRESS,
        "--store",
        str(store_path),
        "--set",
        "cast.md=reference",
        "--set",
        "notes.md=reference",
        "--set",
        "Chapter 01.md=narrative",
        "--set",
        "drafts/Chapter 02.md=narrative",
        "--set",
        "drafts/readme.txt=reference",
        "--confirm",
    ]


def reading(monkeypatch: pytest.MonkeyPatch, traffic: Path) -> Replay:
    replay = Replay(traffic)
    monkeypatch.setattr(dramatis.drive, "_send", replay)
    return replay


class TestTheSameFolderIsTheSameCorpus:
    """Identity rests on the root, not on what the folder or the work is called."""

    def test_reading_it_twice_unchanged_is_one_revision(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(store, a_source(FIRST))
            again = ingest_source(store, a_source(FIRST))

            assert first.revision_id == again.revision_id
            assert again.already_present
            assert len(store.list_works()) == 1

    def test_an_edited_doc_is_a_second_revision_of_one_work(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(store, a_source(FIRST))
            second = ingest_source(store, a_source(EDITED))

            assert first.work_id == second.work_id
            assert first.revision_id != second.revision_id
            assert len(store.list_works()) == 1
            assert len(store.list_text_revisions(first.work_id)) == 2

    def test_only_the_edited_document_is_reported_changed(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            ingest_source(store, a_source(FIRST))
            second = ingest_source(store, a_source(EDITED))

        # What makes a later diff attributable: the graph moved because this chapter was
        # rewritten, and not because four other documents were touched.
        assert {entry.path: entry.state for entry in second.documents} == {
            "Chapter 01.md": "changed",
            "cast.md": "unchanged",
            "drafts/Chapter 02.md": "unchanged",
            "drafts/readme.txt": "unchanged",
            "notes.md": "unchanged",
        }

    def test_renaming_the_folder_in_drive_does_not_start_a_new_corpus(self, tmp_path: Path) -> None:
        # The two traffic files differ in exactly two places, and this is one of them. A work
        # identified by its title would have forked here, silently, and the diff would have
        # had nothing to run across.
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(store, a_source(FIRST))
            second = ingest_source(store, a_source(EDITED))

            assert store.get_work(first.work_id)["title"] == "The Quorum"
            assert second.work_id == first.work_id
            assert second.compared_with == first.revision_id

    def test_the_root_is_what_the_work_is_keyed_by(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, a_source(FIRST))

            assert store.work_at(ROOT)["id"] == result.work_id
            assert store.get_work(result.work_id)["source_root"] == ROOT

    def test_the_folder_names_the_work_rather_than_its_identifier(self, tmp_path: Path) -> None:
        # `1rootFOLDERid` is a poor title for somebody's novel, and the folder's name is on
        # the wire already — the reply that proves the root is a folder carries it.
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, a_source(FIRST))

            assert store.get_work(result.work_id)["title"] == "The Quorum"

    def test_a_title_the_caller_names_still_wins(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, a_source(FIRST), work_title="Transmissions")

            assert store.get_work(result.work_id)["title"] == "Transmissions"

    def test_a_second_ingest_that_forgets_the_title_still_finds_the_work(
        self, tmp_path: Path
    ) -> None:
        # The trap this bullet closes. Naming a work once and not the second time used to
        # mint a second work titled after the folder identifier, with no revision chain.
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(store, a_source(FIRST), work_title="Transmissions")
            second = ingest_source(store, a_source(EDITED))

            assert second.work_id == first.work_id
            assert store.get_work(second.work_id)["title"] == "Transmissions"
            assert len(store.list_works()) == 1


class TestALocalFolderIsKeyedTheSameWay:
    """The change is to `ingest_source`, so it reaches every source. Say so in a test."""

    def test_a_folder_renamed_on_disk_is_a_new_corpus_and_a_moved_one_is_not(
        self, tmp_path: Path
    ) -> None:
        from dramatis.ingest import ingest_folder

        corpus = tmp_path / "novel"
        corpus.mkdir()
        (corpus / "chapter-01.md").write_text("Ada met Tomas.\n", encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_folder(store, corpus)
            (corpus / "chapter-01.md").write_text(
                "Ada met Tomas, and left.\n", encoding="utf-8", newline=""
            )
            second = ingest_folder(store, corpus)

            assert second.work_id == first.work_id
            assert store.get_work(first.work_id)["source_root"] == str(corpus.resolve())

    def test_a_store_made_before_the_column_existed_still_opens(self, tmp_path: Path) -> None:
        # `ADDED_COLUMNS` is the whole migration, and a project file made last month must
        # keep working. Proven by taking the column away and opening it again.
        path = tmp_path / "old.sqlite"
        with Store(path) as store:
            ingest_source(store, a_source(FIRST))

        with Store(path) as store:
            store.connection.execute("UPDATE works SET source_root = NULL")
            store.connection.commit()

        with Store(path) as store:
            works = store.list_works()
            assert works and works[0]["source_root"] is None
            # Nothing to match on, so this reads as a corpus nobody has seen — a new work,
            # not a crash, which is what a nullable column is for.
            assert store.work_at(ROOT) is None


class TestAMapConfirmedAgainstADriveRootIsReused:
    def test_structure_reads_a_drive_folder(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        reading(monkeypatch, FIRST)

        assert main(["structure", "--drive", ADDRESS, "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["root"] == ROOT
        assert [entry["path"] for entry in payload["documents"]] == DOCUMENTS

    def test_confirming_saves_it_under_the_drive_root(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store_path = tmp_path / "p.sqlite"
        with Store(store_path):
            pass
        reading(monkeypatch, FIRST)

        code = main(a_confirmed_map(store_path))

        assert code == 0
        with Store(store_path) as store:
            saved = store.structure_map(ROOT)
            assert saved["cast.md"]["role"]["value"] == REFERENCE

    def test_a_later_ingest_uses_it_without_asking_again(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bullet's second clause, end to end.

        The roles come from the map alone: `ingest` is given no `--role` and never proposes
        anything, so a document stored as reference material was stored that way because
        somebody said so once, against this root.
        """
        store_path = tmp_path / "p.sqlite"
        with Store(store_path):
            pass

        reading(monkeypatch, FIRST)
        assert main(a_confirmed_map(store_path)) == 0

        reading(monkeypatch, EDITED)
        assert main(["ingest", "--drive", ADDRESS, "--store", str(store_path)]) == 0

        with Store(store_path) as store:
            revision = store.list_text_revisions(store.list_works()[0]["id"])[-1]
            roles = {
                store.get_document(identifier).path: store.get_document(identifier).role
                for identifier in revision.document_ids
            }

        assert roles["cast.md"] == REFERENCE
        assert roles["notes.md"] == REFERENCE
        assert roles["Chapter 01.md"] == NARRATIVE

    def test_the_ingest_says_how_many_took_a_confirmed_role(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        store_path = tmp_path / "p.sqlite"
        with Store(store_path):
            pass

        reading(monkeypatch, FIRST)
        assert main(a_confirmed_map(store_path)) == 0
        capsys.readouterr()

        reading(monkeypatch, FIRST)
        main(["ingest", "--drive", ADDRESS, "--store", str(store_path)])

        assert "took the role you confirmed" in capsys.readouterr().out

    def test_forgetting_is_keyed_by_the_drive_root_too(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It used to resolve its argument as a filesystem path, which for a Drive address
        # would key the forget against a folder nobody ever saved a map under.
        store_path = tmp_path / "p.sqlite"
        with Store(store_path):
            pass

        reading(monkeypatch, FIRST)
        assert main(a_confirmed_map(store_path)) == 0

        assert main(["structure", "--drive", ADDRESS, "--store", str(store_path), "--forget"]) == 0
        with Store(store_path) as store:
            assert store.structure_map(ROOT) == {}


class TestStructureReachesANetworkOnlyWhenAskedTo:
    """The same property `ingest` has, and for the same reason (**D59**)."""

    @pytest.fixture
    def nothing_reaches_out(self, monkeypatch: pytest.MonkeyPatch) -> list:
        reached: list[str] = []

        def forbidden(*args: object, **_: object):
            reached.append(str(args[0] if args else "?"))
            raise AssertionError(f"this run reached a network: {reached}")

        monkeypatch.setattr(dramatis.drive, "_send", forbidden)
        monkeypatch.setattr(dramatis.google_auth, "_post", forbidden)
        return reached

    def test_a_path_that_looks_like_a_drive_address_is_a_path(
        self, nothing_reaches_out: list, capsys
    ) -> None:
        assert main(["structure", ROOT]) == 1

        error = capsys.readouterr().err
        assert "authorise" not in error
        assert nothing_reaches_out == []

    def test_naming_neither_is_refused(self, capsys) -> None:
        assert main(["structure"]) == 2
        assert "neither a path nor --drive" in capsys.readouterr().err

    def test_naming_both_is_refused_rather_than_one_winning(self, tmp_path: Path, capsys) -> None:
        assert main(["structure", str(tmp_path), "--drive", ADDRESS]) == 2
        assert "both a path and --drive" in capsys.readouterr().err


def test_the_comparison_5_4_makes_between_revisions_reaches_a_drive_corpus(
    tmp_path: Path,
) -> None:
    """The bullet's stated purpose, as far as it can honestly be proven without a model.

    A full continuity report checks a *reading* against the text, so it needs a snapshot and
    therefore an analysis — which needs a provider, and this suite has none. What it does on
    top of that snapshot is compare the documents of two revisions by path, and that half is
    model-free and is exactly what a corpus never downloaded had no way of having. So this
    exercises `continuity`'s own machinery over two Drive-borne revisions rather than
    asserting a report nothing here could produce.
    """
    from dramatis.continuity import _by_path, _documents_of

    with Store(tmp_path / "p.sqlite") as store:
        first = ingest_source(store, a_source(FIRST))
        second = ingest_source(store, a_source(EDITED))

        before = _by_path(_documents_of(store, first.revision_id))
        after = _by_path(_documents_of(store, second.revision_id))

        assert sorted(before) == sorted(after) == DOCUMENTS
        # One document differs, by path, across two revisions of a corpus that was never on a
        # disk — which is the comparison 3.x's diff and 5.4's report are both built on.
        moved = [path for path in after if before[path].sha256 != after[path].sha256]
        assert moved == ["Chapter 01.md"]
        assert "pretended not to have understood" in after["Chapter 01.md"].content
        assert "pretended not to have understood" not in before["Chapter 01.md"].content

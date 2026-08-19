"""A corpus read from a Google Drive folder (4.13).

The bullet asks for three things, and each has a class here. A folder tree is walked and its
Google Docs exported as Markdown. Anything unreadable is skipped *with its reason*, as a
local folder's non-text files already are. Identity is unchanged — **D32**'s hash is taken
over the exported text, so an edited Doc becomes a new document and a new revision exactly as
an edited file does.

**Nothing here touches a network.** Every test replays `tests/traffic/drive-folder.json`
through an injected transport, or builds a two-line transport of its own for one error. The
`live` marker exists for re-recording that file and is deselected by default, so an ordinary
run has no way to reach Google even if a credential happens to be lying about.

**The traffic file is not yet real, and says so.** It is written to the documented shape of
the Drive v3 API rather than captured from an account, because the credential flow that would
let anyone capture it is **4.14**. `TestTheTrafficIsHonestAboutItself` holds that claim to the
file's own `recorded` field, so the day somebody re-records it the fixture stops describing
itself as synthetic and the test says so.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path

import pytest

from dramatis.drive import (
    DOCUMENT_MIME,
    EXPORT_MIME,
    HOST,
    READONLY_SCOPE,
    DriveSource,
    folder_id,
    root_of,
)
from dramatis.ingest import ingest_source
from dramatis.sources import IngestError, Source
from dramatis.store import Store
from dramatis.structure import propose_structure
from tests.drive_traffic import TRAFFIC, MissingExchange, Recorder, Replay, Traffic

FOLDER = "1rootFOLDERid"
TRAFFIC_FILE = TRAFFIC / "drive-folder.json"


@pytest.fixture
def replay() -> Replay:
    return Replay(TRAFFIC_FILE)


@pytest.fixture
def drive(replay: Replay) -> DriveSource:
    return DriveSource(FOLDER, credentials="a-token", transport=replay)


def a_transport(status: int = 200, body: str = "{}", *, seen: list | None = None):
    """A transport answering everything the same way, for the error paths."""

    def send(method: str, url: str, headers, timeout: float) -> bytes:
        if seen is not None:
            seen.append({"method": method, "url": url, "headers": dict(headers)})
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "no", {}, io.BytesIO(body.encode("utf-8")))
        return body.encode("utf-8")

    return send


def a_folder_reply() -> str:
    return json.dumps({"id": FOLDER, "name": "F", "mimeType": "application/vnd.google-apps.folder"})


class TestTheRootIsStable:
    """The Drive answer to `Path.resolve()`.

    The root keys a confirmed structure map. If a pasted address and a bare identifier gave
    two roots, somebody who confirmed a map from the browser would be asked all over again
    the next time they pasted the id, and their first answers would sit in the store attached
    to a root nothing would look up.
    """

    def test_every_way_of_naming_one_folder_gives_one_root(self) -> None:
        by_id = DriveSource(FOLDER).root
        by_url = DriveSource(f"https://drive.google.com/drive/folders/{FOLDER}").root
        by_deep_url = DriveSource(
            f"https://drive.google.com/drive/u/0/folders/{FOLDER}?usp=sharing"
        ).root
        by_root = DriveSource(root_of(FOLDER)).root

        assert by_id == by_url == by_deep_url == by_root == f"gdrive:folder/{FOLDER}"

    def test_the_root_says_which_kind_of_source_it_came_from(self) -> None:
        # It must never be mistakable for a path somebody once had on a laptop.
        assert DriveSource(FOLDER).root.startswith("gdrive:")

    def test_a_drive_source_satisfies_the_interface(self) -> None:
        assert isinstance(DriveSource(FOLDER), Source)

    @pytest.mark.parametrize(
        "named",
        [
            "",
            "   ",
            "https://example.com/drive/folders/abc",
            "https://drive.google.com/file/d/abc/view",
            "not an id/with a slash",
            "'; drop the query",
        ],
    )
    def test_something_that_is_not_a_folder_is_refused_before_anything_is_contacted(
        self, named: str
    ) -> None:
        # Parsed at construction, so a typo is a message rather than a request. The last case
        # is the one that matters: an identifier is interpolated into the `q` Drive parses.
        with pytest.raises(IngestError):
            DriveSource(named)

    def test_a_file_address_says_what_to_paste_instead(self) -> None:
        with pytest.raises(IngestError, match="drive/folders"):
            folder_id("https://drive.google.com/drive/my-drive")


class TestConstructingASourceContactsNothing:
    """Invariant 7: a named source is contacted only while ingesting.

    Building one, asking it its root, and putting it in a list are all things that must not
    reach Google — otherwise "never contacted unless a person named it in that run" is a
    property of the CLI rather than of the source.
    """

    def test_building_a_source_and_asking_its_root_makes_no_request(self) -> None:
        seen: list = []
        source = DriveSource(FOLDER, credentials="a-token", transport=a_transport(seen=seen))
        _ = source.root

        assert seen == []

    def test_reading_is_what_reaches_out(self, drive: DriveSource, replay: Replay) -> None:
        drive.read()
        assert replay.calls != []

    def test_every_request_is_a_get_to_one_host(self, drive: DriveSource, replay: Replay) -> None:
        drive.read()

        assert {call["method"] for call in replay.calls} == {"GET"}
        assert all(url.startswith(f"https://{HOST}/drive/v3/") for url in replay.urls)

    def test_the_credential_is_sent_as_a_bearer_token(
        self, drive: DriveSource, replay: Replay
    ) -> None:
        drive.read()
        assert {call["headers"]["Authorization"] for call in replay.calls} == {"Bearer a-token"}

    def test_a_credential_may_be_produced_when_asked(self, replay: Replay) -> None:
        # The shape 4.14 hands over: a token minted at the moment of use rather than held.
        asked: list[int] = []

        def token() -> str:
            asked.append(1)
            return "fresh"

        DriveSource(FOLDER, credentials=token, transport=replay).read()

        assert asked != []
        assert {call["headers"]["Authorization"] for call in replay.calls} == {"Bearer fresh"}

    def test_no_credential_at_all_is_refused_rather_than_sent_empty(self) -> None:
        source = DriveSource(FOLDER, transport=a_transport())
        with pytest.raises(IngestError, match="no Google credential"):
            source.read()

    def test_the_scope_asked_for_is_read_only(self) -> None:
        # Invariant 7 says a named source is read-only. This is where that stops being a
        # promise Dramatis makes and becomes one Google enforces.
        assert READONLY_SCOPE.endswith("drive.readonly")


class TestWalkingTheTree:
    def test_the_documents_are_every_readable_thing_in_the_tree(self, drive: DriveSource) -> None:
        reading = drive.read()

        assert [path for path, _ in reading.documents] == [
            "Chapter 01.md",
            "cast.md",
            "drafts/Chapter 02.md",
            "drafts/readme.txt",
            "notes.md",
        ]

    def test_a_subfolder_becomes_a_path_and_not_a_document(self, drive: DriveSource) -> None:
        paths = [path for path, _ in drive.read().documents]

        assert "drafts" not in paths
        assert "drafts/Chapter 02.md" in paths

    def test_pagination_is_followed_to_the_end(self, drive: DriveSource, replay: Replay) -> None:
        # The second page holds five of the tree's twelve entries. A walk that stopped at the
        # first would produce a corpus missing them, with nothing on screen to say so.
        drive.read()

        assert any("pageToken=page-2" in url for url in replay.urls)

    def test_the_order_is_by_path_and_not_by_what_drive_returned(self, drive: DriveSource) -> None:
        # The order decides the revision hash, and the order Drive returns pages in is not a
        # promise. `notes.md` is on page one and `Chapter 01` before it; sorted, they swap.
        paths = [path for path, _ in drive.read().documents]

        assert paths == sorted(paths)

    def test_a_folder_that_is_not_a_folder_says_so(self) -> None:
        reply = json.dumps({"id": FOLDER, "name": "x", "mimeType": DOCUMENT_MIME})
        source = DriveSource(FOLDER, credentials="t", transport=a_transport(body=reply))

        with pytest.raises(IngestError, match="is not a folder"):
            source.read()

    def test_a_folder_that_is_not_there_is_a_typo_rather_than_an_empty_corpus(self) -> None:
        # `files.list` answers a query about a nonexistent parent with an empty list, so
        # without the root check a mistyped identifier reads as a folder holding nothing.
        source = DriveSource(FOLDER, credentials="t", transport=a_transport(404, "{}"))

        with pytest.raises(IngestError, match="404"):
            source.read()


class TestReadingADocument:
    def test_a_google_doc_is_exported_as_markdown(self, drive: DriveSource, replay: Replay) -> None:
        reading = drive.read()

        assert any(f"mimeType={EXPORT_MIME.replace('/', '%2F')}" in url for url in replay.urls)
        assert dict(reading.documents)["cast.md"].startswith("# The cast")

    def test_a_doc_lands_on_md_so_every_suffix_rule_downstream_applies_to_it(
        self, drive: DriveSource
    ) -> None:
        # D56's reason for choosing Markdown: it keeps the headings structure inference reads
        # and lands on a suffix the rest of the project already treats as text.
        assert "cast.md" in dict(drive.read().documents)

    def test_an_uploaded_text_file_is_downloaded_as_it_stands(self, drive: DriveSource) -> None:
        assert dict(drive.read().documents)["drafts/readme.txt"].startswith("Drafts in progress")

    def test_line_endings_are_normalised_as_a_file_read_from_disk_is(self) -> None:
        exported = "# One\r\n\r\nAda met Tomas.\r\n"
        transport = _a_tree_with(exported)
        text = dict(DriveSource(FOLDER, credentials="t", transport=transport).read().documents)

        assert text["only.md"] == "# One\n\nAda met Tomas.\n"


class TestAnythingItCannotReadIsSkippedWithItsReason:
    """The promise 4.12 made the interface's rather than a folder's habit.

    A revision quietly missing a chapter is a graph missing a character, with nothing on
    screen to say why. Every branch below is a real thing in a real Drive folder.
    """

    def test_every_skip_names_the_document_and_the_reason(self, drive: DriveSource) -> None:
        assert [path for path, _ in drive.read().skipped] == [
            "Appendix.md",
            "Chapter 03",
            "Ledger",
            "Whole Novel.md",
            "cover.png",
            "notes.md",
        ]

    @pytest.mark.parametrize(
        ("path", "reason"),
        [
            ("cover.png", "not a text file (.png)"),
            ("Ledger", "a Google spreadsheet, which has no text to read"),
            ("Chapter 03", "a shortcut, which Dramatis does not follow"),
            ("Appendix.md", "Appendix is empty"),
            ("Whole Novel.md", "too large for Drive to export (10MB)"),
            ("notes.md", "a second document is already at this path"),
        ],
    )
    def test_the_reason_says_what_a_person_would_have_to_change(
        self, drive: DriveSource, path: str, reason: str
    ) -> None:
        assert reason in dict(drive.read().skipped)[path]

    def test_a_non_text_upload_is_skipped_by_the_same_rule_a_folder_uses(
        self, drive: DriveSource
    ) -> None:
        # Word for word what `FileSystemSource` says about the same file, because it is the
        # same rule: the suffix decides, and anything else is reported rather than passed over.
        assert dict(drive.read().skipped)["cover.png"] == "not a text file (.png)"

    def test_a_document_that_is_skipped_is_never_fetched(
        self, drive: DriveSource, replay: Replay
    ) -> None:
        # The collision loser, the sheet, the shortcut and the image are all decided from the
        # listing alone. Fetching them would cost a request and a quota for nothing.
        drive.read()

        assert not any("id-file-notes" in url for url in replay.urls)
        assert not any("id-sheet-ledger" in url for url in replay.urls)

    def test_which_of_two_colliding_documents_survives_does_not_depend_on_drive(self) -> None:
        # Drive lets two things in one folder share a name, and downstream keys documents by
        # path. Whichever wins, it must be the same one on every read.
        first = Replay(TRAFFIC_FILE)
        second = Replay(TRAFFIC_FILE)
        kept = DriveSource(FOLDER, credentials="t", transport=first).read()
        again = DriveSource(FOLDER, credentials="t", transport=second).read()

        assert kept.documents == again.documents
        assert dict(kept.documents)["notes.md"].startswith("Ada Mbeki is")

    def test_text_that_is_not_utf8_is_refused_rather_than_guessed(self) -> None:
        def transport(method: str, url: str, headers, timeout: float) -> bytes:
            if "/export" in url:
                return b"Ada met Bram\xe9 at the gate.\n"
            return _tree_reply(url, "only", DOCUMENT_MIME)

        reading = DriveSource(FOLDER, credentials="t", transport=transport).read()

        assert reading.documents == ()
        assert "not valid UTF-8" in dict(reading.skipped)["only.md"]


class TestWhatCannotBeReadAtAllRaises:
    """One document failing is a skip; the corpus failing is an error.

    A revision built from the half of a corpus that happened to answer is worse than no
    revision at all, because nothing downstream can tell it apart from a corpus that shrank.
    """

    @pytest.mark.parametrize(
        ("status", "says"),
        [
            (401, "rejected the credential"),
            (403, "refused access"),
            (429, "rate-limiting"),
            (500, "unavailable"),
        ],
    )
    def test_a_failure_while_walking_stops_the_ingest(self, status: int, says: str) -> None:
        source = DriveSource(FOLDER, credentials="t", transport=a_transport(status, "{}"))

        with pytest.raises(IngestError, match=says):
            source.read()

    def test_a_reply_that_is_not_json_is_named_rather_than_crashing(self) -> None:
        source = DriveSource(FOLDER, credentials="t", transport=a_transport(200, "<html>no</html>"))

        with pytest.raises(IngestError, match="not JSON"):
            source.read()

    def test_a_network_that_is_not_there_says_which_host_was_wanted(self) -> None:
        def transport(*_: object) -> bytes:
            raise urllib.error.URLError("nodename nor servname provided")

        source = DriveSource(FOLDER, credentials="t", transport=transport)
        with pytest.raises(IngestError, match="could not reach Google Drive"):
            source.read()


class TestIdentityIsUnchanged:
    """The bullet's third claim, and the one that makes revisions work over Drive.

    D32 identifies a document by its path and the hash of its content. Taking the hash over
    the *exported* text means an edited Doc is a new document in a new revision exactly as an
    edited file is — with no code downstream aware that a network was involved.
    """

    def test_a_drive_corpus_ingests_like_any_other(self, drive: DriveSource, tmp_path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result = ingest_source(store, drive, work_title="W", collection_name="C")

            assert [entry.path for entry in result.documents] == [
                "Chapter 01.md",
                "cast.md",
                "drafts/Chapter 02.md",
                "drafts/readme.txt",
                "notes.md",
            ]
            assert store.revision_text(result.revision_id).count("Ada") >= 3

    def test_an_edited_doc_becomes_a_new_document_and_a_new_revision(self, tmp_path) -> None:
        before = _a_tree_with("# One\n\nAda met Tomas at the relay.\n")
        after = _a_tree_with("# One\n\nAda met Tomas at the relay, and did not stay.\n")

        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(
                store,
                DriveSource(FOLDER, credentials="t", transport=before),
                work_title="W",
                collection_name="C",
            )
            second = ingest_source(
                store,
                DriveSource(FOLDER, credentials="t", transport=after),
                work_title="W",
                collection_name="C",
            )

            assert first.revision_id != second.revision_id
            assert first.documents[0].document_id != second.documents[0].document_id
            assert [entry.state for entry in second.documents] == ["changed"]
            # The older revision still returns what it held — the promise D32 exists for.
            assert "and did not stay" not in store.revision_text(first.revision_id)

    def test_an_unedited_doc_ingests_to_the_same_revision_twice(self, tmp_path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            first = ingest_source(
                store,
                DriveSource(FOLDER, credentials="t", transport=Replay(TRAFFIC_FILE)),
                work_title="W",
                collection_name="C",
            )
            again = ingest_source(
                store,
                DriveSource(FOLDER, credentials="t", transport=Replay(TRAFFIC_FILE)),
                work_title="W",
                collection_name="C",
            )

            assert first.revision_id == again.revision_id
            assert again.already_present

    def test_a_structure_map_is_proposed_over_a_drive_corpus(self, drive: DriveSource) -> None:
        # 4.12's claim, exercised by the source it was built for: structure inference reads
        # pairs, so it reaches a Drive corpus with no change at all.
        structure = propose_structure(drive)

        assert structure.root == f"gdrive:folder/{FOLDER}"
        assert [plan.path for plan in structure.documents] == [
            "Chapter 01.md",
            "cast.md",
            "drafts/Chapter 02.md",
            "drafts/readme.txt",
            "notes.md",
        ]

    def test_the_structure_map_is_keyed_by_the_drive_root(self, drive: DriveSource, tmp_path):
        # What 4.15 will reuse: a map confirmed against a Drive root is found again by it.
        with Store(tmp_path / "p.sqlite") as store:
            store.save_structure_map(drive.root, {"cast.md": {"path": "cast.md"}}, "2026-01-01Z")

            assert list(store.structure_map(drive.root)) == ["cast.md"]


class TestTheTrafficIsHonestAboutItself:
    """A fixture nobody verified launders a guess into a reference (the note under 0.6).

    The traffic file states whether it was captured or written, and these hold it to that.
    When somebody re-records it against a real folder, `recorded` becomes true and the second
    test starts failing — which is the reminder to delete it and this paragraph.
    """

    def test_the_file_says_whether_it_was_captured_or_written(self) -> None:
        traffic = Traffic.load(TRAFFIC_FILE)

        assert "recorded" in json.loads(TRAFFIC_FILE.read_text(encoding="utf-8"))
        assert traffic.note != ""

    def test_it_is_still_written_rather_than_captured(self) -> None:
        assert Traffic.load(TRAFFIC_FILE).recorded is False, (
            "the traffic file now claims to be captured from a real Drive. Delete this test "
            "and the paragraph in this module's docstring that says it is not."
        )

    def test_a_request_the_file_does_not_hold_fails_naming_what_was_asked_for(self) -> None:
        # The stale-recording failure mode: change what the source asks for and the answer is
        # simply not found, rather than an answer to a question nobody asks any more.
        source = DriveSource("someOtherFolder", credentials="t", transport=Replay(TRAFFIC_FILE))

        with pytest.raises(MissingExchange, match="no recorded exchange"):
            source.read()


@pytest.mark.live
class TestAgainstARealDrive:
    """Re-recording. Deselected by default and never run in CI.

    Needs a folder identifier and an access token in the environment, which is all the
    credential handling this bullet has; **4.14** replaces the token with an OAuth flow. Run
    it deliberately:

        DRAMATIS_DRIVE_FOLDER=... DRAMATIS_DRIVE_TOKEN=... pytest -m live -k RealDrive

    Record only from a folder you are willing to commit: an exported Doc is somebody's
    corpus, exactly as a checkpoint's prompt is.
    """

    def test_recording_a_real_folder(self) -> None:
        folder = os.environ.get("DRAMATIS_DRIVE_FOLDER")
        token = os.environ.get("DRAMATIS_DRIVE_TOKEN")
        if not folder or not token:
            pytest.skip("set DRAMATIS_DRIVE_FOLDER and DRAMATIS_DRIVE_TOKEN to re-record")

        traffic = Traffic(Path(os.environ.get("DRAMATIS_DRIVE_TRAFFIC") or TRAFFIC_FILE))
        traffic.note = "Google Drive v3 exchanges captured from a real folder."
        source = DriveSource(folder, credentials=token, transport=Recorder(traffic))

        reading = source.read()

        assert reading.documents, "the folder held nothing readable"
        traffic.save()


# -- helpers ------------------------------------------------------------------------------


def _tree_reply(url: str, name: str, mime: str) -> bytes:
    """A one-document tree: the root check, then a single listing."""
    if "/files?q=" in url:
        return json.dumps({"files": [{"id": "id-only", "name": name, "mimeType": mime}]}).encode()
    return a_folder_reply().encode()


def _a_tree_with(exported: str):
    """A transport serving one Google Doc named `only`, exporting the given text."""

    def transport(method: str, url: str, headers, timeout: float) -> bytes:
        if "/export" in url:
            return exported.encode("utf-8")
        return _tree_reply(url, "only", DOCUMENT_MIME)

    return transport

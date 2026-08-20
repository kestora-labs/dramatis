"""`dramatis authorise`, and `dramatis ingest --drive` (4.14).

The bullet's last sentence is the one with teeth: *refused unless the run names a Drive
source, so a typo cannot reach the network*. `TestATypoCannotReachTheNetwork` is that
sentence as a test, and it is written the only way it can honestly be written — by making the
real transport explode if anything calls it, and then asking the command to ingest things
that look exactly like Drive addresses.

Everything else replays **4.13**'s recorded traffic through an injected transport and mints
tokens through an injected one, so an ordinary run reaches neither Google's API nor its
sign-in.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

import dramatis.drive
import dramatis.google_auth
from dramatis.cli import main
from dramatis.drive import READONLY_SCOPE
from dramatis.store import Store
from tests.drive_traffic import TRAFFIC, Replay

FOLDER = "1rootFOLDERid"
TRAFFIC_FILE = TRAFFIC / "drive-folder.json"
ADDRESS = f"https://drive.google.com/drive/folders/{FOLDER}"


@pytest.fixture
def nothing_reaches_out(monkeypatch: pytest.MonkeyPatch):
    """Both real transports, replaced by something that fails loudly if it is called.

    A test that asserts "no request was made" by inspecting a spy proves only that the spy
    was not called. This makes the actual code paths unusable, so a command that reached for
    a network at all would fail rather than quietly pass.
    """
    reached: list[str] = []

    def forbidden(*args: object, **_: object):
        reached.append(str(args[0] if args else "?"))
        raise AssertionError(f"this run reached a network: {reached}")

    monkeypatch.setattr(dramatis.drive, "_send", forbidden)
    monkeypatch.setattr(dramatis.google_auth, "_post", forbidden)
    return reached


@pytest.fixture
def authorised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cached credential, in a temporary place, that mints a token without Google."""
    path = tmp_path / "credential" / "google-drive.json"
    path.parent.mkdir(parents=True, exist_ok=True)
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

    def token(url: str, payload: bytes, headers, timeout: float) -> bytes:
        return json.dumps(
            {"access_token": "ya29.access", "expires_in": 3599, "scope": READONLY_SCOPE}
        ).encode("utf-8")

    monkeypatch.setattr(dramatis.google_auth, "_post", token)
    return path


@pytest.fixture
def drive_traffic(monkeypatch: pytest.MonkeyPatch) -> Replay:
    replay = Replay(TRAFFIC_FILE)
    monkeypatch.setattr(dramatis.drive, "_send", replay)
    return replay


class TestATypoCannotReachTheNetwork:
    """Whether a run reaches Google is decided by `--drive` and by nothing else.

    Sniffing the positional argument would be the obvious convenience and the wrong one: it
    would mean a mistyped path could send a folder name to Google, and a person could not
    tell by reading the command whether it was about to.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "gdrive:folder/1rootFOLDERid",
            "https://drive.google.com/drive/folders/1rootFOLDERid",
            "1rootFOLDERid",
        ],
    )
    def test_a_path_that_looks_like_a_drive_address_is_a_path(
        self, path: str, tmp_path: Path, nothing_reaches_out: list, capsys
    ) -> None:
        assert main(["ingest", path, "--store", str(tmp_path / "p.sqlite")]) == 1

        # It failed as a path — the message names the argument and says it could not be read
        # — and nowhere in it is Google, Drive, or an invitation to authorise. The exact
        # wording is the operating system's, so what is asserted is which *kind* of failure
        # this was rather than its sentence.
        error = capsys.readouterr().err
        assert "1rootFOLDERid" in error
        assert "authorise" not in error
        assert "Drive" not in error
        assert nothing_reaches_out == []

    def test_ingest_without_a_corpus_names_the_problem(self, tmp_path: Path, capsys) -> None:
        assert main(["ingest", "--store", str(tmp_path / "p.sqlite")]) == 2
        assert "neither a path nor --drive" in capsys.readouterr().err

    def test_ingest_with_both_refuses_rather_than_choosing(self, tmp_path: Path, capsys) -> None:
        # Which one won would be a rule nobody could guess, on a flag that decides whether a
        # manuscript leaves the machine.
        code = main(["ingest", "x.txt", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite")])

        assert code == 2
        assert "both a path and --drive" in capsys.readouterr().err

    def test_a_mistyped_drive_address_is_refused_before_anything_is_contacted(
        self, tmp_path: Path, nothing_reaches_out: list, capsys
    ) -> None:
        code = main(
            ["ingest", "--drive", "https://example.com/folders/x", "--store", str(tmp_path / "p")]
        )

        assert code == 1
        assert "not a Google Drive address" in capsys.readouterr().err
        assert nothing_reaches_out == []

    def test_an_unauthorised_machine_says_which_command_to_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nothing_reaches_out: list, capsys
    ) -> None:
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "absent.json"))

        assert main(["ingest", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite")]) == 1
        assert "dramatis authorise" in capsys.readouterr().err
        assert nothing_reaches_out == []


class TestIngestingADriveFolder:
    def test_a_drive_corpus_becomes_a_revision(
        self, tmp_path: Path, authorised: Path, drive_traffic: Replay, capsys
    ) -> None:
        store = tmp_path / "p.sqlite"
        code = main(["ingest", "--drive", ADDRESS, "--store", str(store), "--work", "W", "--json"])

        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert [entry["path"] for entry in payload["documents"]] == [
            "Chapter 01.md",
            "cast.md",
            "drafts/Chapter 02.md",
            "drafts/readme.txt",
            "notes.md",
        ]

    def test_the_folder_address_and_its_identifier_reach_the_same_project(
        self, tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The root is what a confirmed structure map is keyed by, so the two spellings must
        # not produce two revisions of two works.
        store = tmp_path / "p.sqlite"
        for named in (ADDRESS, FOLDER):
            monkeypatch.setattr(dramatis.drive, "_send", Replay(TRAFFIC_FILE))
            assert main(["ingest", "--drive", named, "--store", str(store), "--work", "W"]) == 0

        with Store(store) as opened:
            works = opened.list_works()
            assert len(works) == 1
            assert len(opened.list_text_revisions(works[0]["id"])) == 1

    def test_the_root_is_reported_so_it_can_be_used_again(
        self, tmp_path: Path, authorised: Path, drive_traffic: Replay, capsys
    ) -> None:
        main(["ingest", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite"), "--work", "W"])

        assert f"gdrive:folder/{FOLDER}" in capsys.readouterr().out

    def test_what_drive_could_not_read_is_reported_on_stderr(
        self, tmp_path: Path, authorised: Path, drive_traffic: Replay, capsys
    ) -> None:
        main(["ingest", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite"), "--work", "W"])

        errors = capsys.readouterr().err
        assert "skipped cover.png" in errors
        assert "skipped Whole Novel.md" in errors

    def test_one_sign_in_serves_the_whole_walk(
        self, tmp_path: Path, authorised: Path, drive_traffic: Replay, monkeypatch
    ) -> None:
        posted: list = []

        def token(url: str, payload: bytes, headers, timeout: float) -> bytes:
            posted.append(url)
            return json.dumps({"access_token": "ya29.a", "expires_in": 3599}).encode("utf-8")

        monkeypatch.setattr(dramatis.google_auth, "_post", token)
        main(["ingest", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite"), "--work", "W"])

        # Eleven Drive requests, one token exchange.
        assert len(drive_traffic.calls) > 1
        assert len(posted) == 1

    def test_the_credential_never_enters_the_project(
        self, tmp_path: Path, authorised: Path, drive_traffic: Replay
    ) -> None:
        store = tmp_path / "p.sqlite"
        main(["ingest", "--drive", ADDRESS, "--store", str(store), "--work", "W"])

        assert b"1//refresh" not in store.read_bytes()
        assert b"GOCSPX" not in store.read_bytes()


class TestTheAuthoriseCommand:
    def test_status_on_an_unauthorised_machine_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "absent.json"))

        assert main(["authorise", "--status"]) == 1
        assert "dramatis authorise" in capsys.readouterr().err

    def test_status_says_where_and_with_what_scope(
        self, tmp_path: Path, authorised: Path, capsys
    ) -> None:
        assert main(["authorise", "--status"]) == 0

        out = capsys.readouterr().out
        assert str(authorised) in out
        assert READONLY_SCOPE in out

    def test_forgetting_removes_it_and_says_it_is_still_live_at_google(
        self, tmp_path: Path, authorised: Path, capsys
    ) -> None:
        assert main(["authorise", "--forget"]) == 0

        captured = capsys.readouterr()
        assert not authorised.exists()
        # The difference matters and is not obvious: a deleted token stops this machine using
        # the grant and does not end it at Google.
        assert "myaccount.google.com/permissions" in captured.err

    def test_forgetting_nothing_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "absent.json"))

        assert main(["authorise", "--forget"]) == 0
        assert "no Google credential" in capsys.readouterr().out

    def test_without_a_client_secret_it_says_why_dramatis_has_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nothing_reaches_out: list, capsys
    ) -> None:
        monkeypatch.delenv("DRAMATIS_GOOGLE_CLIENT_SECRET", raising=False)
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "absent.json"))

        assert main(["authorise"]) == 1
        assert "shared secret with the whole internet" in capsys.readouterr().err
        assert nothing_reaches_out == []

    def test_a_client_secret_that_is_not_there_is_a_sentence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nothing_reaches_out: list, capsys
    ) -> None:
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "absent.json"))

        code = main(["authorise", "--client-secret", str(tmp_path / "nope.json")])

        assert code == 1
        assert "no client secret" in capsys.readouterr().err
        assert nothing_reaches_out == []

    def test_a_completed_consent_is_saved_outside_any_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from dramatis.google_auth import Consent

        secret = tmp_path / "client.json"
        secret.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "123.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-not-real",
                        "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
            ),
            encoding="utf-8",
        )
        where = tmp_path / "config" / "google-drive.json"
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(where))

        def receiver(auth_url: str, state: str, timeout: float) -> Consent:
            return Consent(code="4/code", redirect_uri="http://127.0.0.1:9/")

        def token(url: str, payload: bytes, headers, timeout: float) -> bytes:
            form = dict(urllib.parse.parse_qsl(payload.decode("utf-8")))
            assert form["grant_type"] == "authorization_code"
            return json.dumps(
                {
                    "access_token": "ya29.a",
                    "refresh_token": "1//refresh",
                    "scope": READONLY_SCOPE,
                    "expires_in": 3599,
                }
            ).encode("utf-8")

        monkeypatch.setattr(dramatis.google_auth, "LoopbackReceiver", lambda *a, **k: receiver)
        monkeypatch.setattr(dramatis.google_auth, "_post", token)

        assert main(["authorise", "--client-secret", str(secret)]) == 0

        saved = json.loads(where.read_text(encoding="utf-8"))
        assert saved["refresh_token"] == "1//refresh"
        assert saved["scope"] == READONLY_SCOPE
        assert "not part of any project" in capsys.readouterr().out

    def test_a_refused_consent_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        from dramatis.google_auth import AuthError

        secret = tmp_path / "client.json"
        secret.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "1.apps.googleusercontent.com",
                        "client_secret": "GOCSPX-x",
                    }
                }
            ),
            encoding="utf-8",
        )
        where = tmp_path / "config" / "google-drive.json"
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(where))

        def declined(*_: object, **__: object):
            raise AuthError("consent was declined in the browser. Nothing was stored.")

        monkeypatch.setattr(dramatis.google_auth, "LoopbackReceiver", lambda *a, **k: declined)

        assert main(["authorise", "--client-secret", str(secret)]) == 1
        assert not where.exists()
        assert "declined" in capsys.readouterr().err


def test_a_drive_ingest_reports_a_refused_folder_as_a_sentence(
    tmp_path: Path, authorised: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The commonest real failure: the account that consented cannot see the folder."""

    def refused(method: str, url: str, headers, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url, 403, "no", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(dramatis.drive, "_send", refused)

    assert main(["ingest", "--drive", ADDRESS, "--store", str(tmp_path / "p.sqlite")]) == 1
    assert "may not have access to this folder" in capsys.readouterr().err

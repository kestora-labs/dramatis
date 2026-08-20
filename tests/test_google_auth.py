"""Consenting once to read Google Drive, and where the answer is kept (4.14).

**Nothing here contacts Google.** The token exchange is an injected transport in every test,
and the one test that opens a real socket binds it to `127.0.0.1` and drives it with a
request this process makes itself — which is the only way to find out whether the loopback
receiver works at all, and reaches nothing off the machine.

Four things are being held still, and each is a claim the bullet makes:

**The refresh token never enters a project.** A project store is a thing people send to each
other, so `TestTheCredentialLivesOutsideTheProject` opens the store afterwards and looks.

**Read-only, and only to Google.** The consent screen asks for `drive.readonly` and nothing
else; the client secret is posted to an allowlisted host and refused anywhere else, because
the address arrives in a file the user downloaded.

**The code is worth nothing to anybody else.** `state` is checked, so a stray redirect cannot
finish somebody's sign-in, and PKCE means an authorisation code seen by another process on
the machine cannot be exchanged.

**One sign-in, not one per request.** A walk of a large folder is hundreds of requests, and a
token exchange per request would turn one consent into a rate limit.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from dramatis.drive import READONLY_SCOPE
from dramatis.google_auth import (
    ALLOWED_HOSTS,
    TOKEN_URI,
    AccessToken,
    AuthError,
    ClientSecret,
    Consent,
    Credential,
    LoopbackReceiver,
    authorisation_url,
    authorise,
    config_dir,
    credential_path,
    forget_credential,
    load_credential,
    save_credential,
)

CLIENT_ID = "123-abc.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-not-a-real-secret"


def a_client_secret(path: Path, **over) -> Path:
    payload = {
        "installed": {
            "client_id": CLIENT_ID,
            "project_id": "dramatis-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": CLIENT_SECRET,
            "redirect_uris": ["http://localhost"],
            **over,
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def a_credential(**over) -> Credential:
    fields = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": "1//refresh",
        "scope": READONLY_SCOPE,
        "obtained_at": "2026-08-20T00:00:00+00:00",
        **over,
    }
    return Credential(**fields)


def a_token_transport(reply: dict | None = None, *, seen: list | None = None, status: int = 200):
    """Stands in for the one request this project makes that carries a secret."""
    body = json.dumps(
        reply
        if reply is not None
        else {
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "scope": READONLY_SCOPE,
            "expires_in": 3599,
            "token_type": "Bearer",
        }
    )

    def send(url: str, payload: bytes, headers, timeout: float) -> bytes:
        if seen is not None:
            seen.append(
                {
                    "url": url,
                    "form": dict(urllib.parse.parse_qsl(payload.decode("utf-8"))),
                    "headers": dict(headers),
                }
            )
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "no", {}, io.BytesIO(body.encode("utf-8")))
        return body.encode("utf-8")

    return send


def a_receiver(code: str = "4/authcode", *, seen: list | None = None):
    """Stands in for a browser. Answers with a code without opening anything."""

    def receive(auth_url: str, state: str, timeout: float) -> Consent:
        if seen is not None:
            seen.append({"url": auth_url, "state": state, "timeout": timeout})
        return Consent(code=code, redirect_uri="http://127.0.0.1:9/")

    return receive


class TestTheClientSecretIsTheUsersOwn:
    """Dramatis ships no OAuth client, for the reason it ships no model keys."""

    def test_a_desktop_client_loads(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "client.json"))

        assert secret.client_id == CLIENT_ID
        assert secret.token_uri == TOKEN_URI

    def test_a_web_client_says_which_kind_to_create_instead(self, tmp_path: Path) -> None:
        path = tmp_path / "web.json"
        path.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}), "utf-8")

        with pytest.raises(AuthError, match="Desktop app"):
            ClientSecret.load(path)

    def test_a_missing_file_is_a_sentence_rather_than_a_traceback(self, tmp_path: Path) -> None:
        with pytest.raises(AuthError, match="no client secret"):
            ClientSecret.load(tmp_path / "absent.json")

    def test_a_document_without_the_fields_says_what_to_download(self, tmp_path: Path) -> None:
        path = tmp_path / "half.json"
        path.write_text(json.dumps({"installed": {"client_id": "x"}}), encoding="utf-8")

        with pytest.raises(AuthError, match="Desktop app"):
            ClientSecret.load(path)

    @pytest.mark.parametrize("uri", ["https://evil.example/token", "http://oauth2.googleapis.com"])
    def test_a_token_address_that_is_not_googles_is_refused(self, tmp_path: Path, uri: str) -> None:
        # The one genuinely dangerous request in this project posts a client secret. Its
        # address arrives in a downloaded file, and a file can be edited or swapped.
        path = a_client_secret(tmp_path / "client.json", token_uri=uri)

        with pytest.raises(AuthError, match="not a Google sign-in address"):
            ClientSecret.load(path)

    def test_the_allowlist_is_google_and_nothing_else(self) -> None:
        assert {"oauth2.googleapis.com", "accounts.google.com"} == ALLOWED_HOSTS


class TestTheConsentScreenAsksForReadOnly:
    def test_the_scope_is_drive_readonly_and_nothing_else(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(authorisation_url(secret, challenge="c", state="s")).query
        )

        assert query["scope"] == [READONLY_SCOPE]
        assert READONLY_SCOPE.endswith("drive.readonly")

    def test_it_asks_for_a_refresh_token_explicitly(self, tmp_path: Path) -> None:
        # Without both of these Google returns an access token and no refresh token on a
        # second authorisation, and the flow appears to work until the hour is up.
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(authorisation_url(secret, challenge="c", state="s")).query
        )

        assert query["access_type"] == ["offline"]
        assert query["prompt"] == ["consent"]

    def test_it_goes_to_google(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        url = authorisation_url(secret, challenge="c", state="s")

        assert urllib.parse.urlparse(url).hostname in ALLOWED_HOSTS


class TestTheCodeIsWorthNothingToAnybodyElse:
    """A loopback port is not private to one process, and a redirect is not authenticated."""

    def test_the_code_is_bound_to_a_verifier_only_this_process_has(self, tmp_path: Path) -> None:
        import base64
        import hashlib

        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        asked, posted = [], []
        authorise(
            secret,
            receiver=a_receiver(seen=asked),
            transport=a_token_transport(seen=posted),
        )

        challenge = urllib.parse.parse_qs(urllib.parse.urlparse(asked[0]["url"]).query)
        verifier = posted[0]["form"]["code_verifier"]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()

        assert challenge["code_challenge_method"] == ["S256"]
        assert challenge["code_challenge"] == [
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        ]

    def test_a_redirect_with_the_wrong_state_is_abandoned(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))

        def wrong_state(auth_url: str, state: str, timeout: float) -> Consent:
            from dramatis.google_auth import _consent_from

            return _consent_from({"code": ["4/x"], "state": ["not-it"]}, state, "http://x/", 1)

        with pytest.raises(AuthError, match="wrong state"):
            authorise(secret, receiver=wrong_state, transport=a_token_transport())

    def test_a_declined_consent_stores_nothing_and_says_so(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))

        def declined(auth_url: str, state: str, timeout: float) -> Consent:
            from dramatis.google_auth import _consent_from

            return _consent_from({"error": ["access_denied"]}, state, "http://x/", 1)

        with pytest.raises(AuthError, match="declined in the browser"):
            authorise(secret, receiver=declined, transport=a_token_transport())

    def test_a_state_is_not_reused_between_flows(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        seen: list = []
        for _ in range(2):
            authorise(secret, receiver=a_receiver(seen=seen), transport=a_token_transport())

        assert seen[0]["state"] != seen[1]["state"]


class TestTheLoopbackReceiver:
    """The socket, exercised by a request this process makes to itself.

    Everything else injects a receiver, because a test that needs a browser tests the browser.
    This one exists because the alternative is shipping the only untested part of the flow.
    """

    def _drive(self, browser_saw: list) -> None:
        """Play the browser: read the address, then fetch the redirect it names.

        The fetch runs in a thread because the receiver is blocking, and the thread is kept so
        the test can wait for the *response* rather than only for the request. Asserting on
        what the browser was shown without joining is a race, and it is one that passes almost
        every time.
        """
        threads: list[threading.Thread] = []

        def visit(url: str) -> None:
            browser_saw.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            redirect = query["redirect_uri"][0]
            state = query["state"][0]

            def fetch() -> None:
                back = f"{redirect}?code=4/loopback&state={urllib.parse.quote(state)}"
                with urllib.request.urlopen(back, timeout=10) as response:  # noqa: S310
                    browser_saw.append(response.read().decode("utf-8"))

            thread = threading.Thread(target=fetch, daemon=True)
            threads.append(thread)
            thread.start()

        self.visit = visit
        self.threads = threads

    def _settle(self) -> None:
        for thread in self.threads:
            thread.join(timeout=10)

    def test_a_browser_coming_back_hands_over_the_code(self) -> None:
        saw: list = []
        self._drive(saw)
        receiver = LoopbackReceiver(open_browser=self.visit)

        consent = receiver("https://accounts.google.com/o/oauth2/v2/auth?state=s", "s", 10.0)

        assert consent.code == "4/loopback"
        assert consent.redirect_uri.startswith("http://127.0.0.1:")

    def test_it_listens_on_loopback_only(self) -> None:
        saw: list = []
        self._drive(saw)

        consent = LoopbackReceiver(open_browser=self.visit)(
            "https://accounts.google.com/o/oauth2/v2/auth?state=s", "s", 10.0
        )

        # Nothing off this machine can reach the listener even while it is up.
        assert urllib.parse.urlparse(consent.redirect_uri).hostname == "127.0.0.1"

    def test_the_browser_is_told_what_to_do_next(self) -> None:
        saw: list = []
        self._drive(saw)
        LoopbackReceiver(open_browser=self.visit)(
            "https://accounts.google.com/o/oauth2/v2/auth?state=s", "s", 10.0
        )
        self._settle()

        assert "close this tab" in saw[-1]


class TestTheCredentialLivesOutsideTheProject:
    """The sentence the bullet is most specific about.

    *A project store is a thing people send to each other, and a credential must not travel
    in one.* So this looks in the project afterwards rather than taking the code's word.
    """

    def test_the_default_home_is_the_users_configuration_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DRAMATIS_GOOGLE_CREDENTIAL", raising=False)

        assert credential_path().parent == config_dir()
        assert config_dir().name == "dramatis"

    def test_nothing_is_written_into_a_project(self, tmp_path: Path, monkeypatch) -> None:
        from dramatis.store import Store

        project = tmp_path / "project.sqlite"
        with Store(project):
            pass
        before = project.read_bytes()

        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "cred.json"))
        save_credential(a_credential())

        assert project.read_bytes() == before
        assert b"refresh" not in project.read_bytes()

    def test_the_cache_is_owner_only_where_the_platform_has_permissions(
        self, tmp_path: Path
    ) -> None:
        saved = save_credential(a_credential(), tmp_path / "cred.json")
        mode = stat.S_IMODE(saved.stat().st_mode)

        if sys.platform == "win32":
            # `chmod` on Windows sets a read-only flag and nothing else, so the honest claim
            # is only that the file exists and holds what it should.
            assert saved.is_file()
        else:
            assert mode == 0o600

    def test_it_round_trips(self, tmp_path: Path) -> None:
        saved = save_credential(a_credential(), tmp_path / "cred.json")

        assert load_credential(saved) == a_credential()

    def test_a_missing_credential_says_which_command_makes_one(self, tmp_path: Path) -> None:
        with pytest.raises(AuthError, match="dramatis authorise"):
            load_credential(tmp_path / "absent.json")

    def test_a_half_written_credential_is_named_rather_than_used(self, tmp_path: Path) -> None:
        path = tmp_path / "cred.json"
        path.write_text(json.dumps({"client_id": "x"}), encoding="utf-8")

        with pytest.raises(AuthError, match="refresh_token"):
            load_credential(path)

    def test_forgetting_says_whether_there_was_one(self, tmp_path: Path) -> None:
        path = tmp_path / "cred.json"

        assert forget_credential(path) is False
        save_credential(a_credential(), path)
        assert forget_credential(path) is True
        assert not path.exists()

    def test_an_environment_override_is_honoured(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", str(tmp_path / "elsewhere.json"))

        assert credential_path() == tmp_path / "elsewhere.json"


class TestWhatComesBackFromTheExchange:
    def test_a_grant_without_a_refresh_token_is_refused(self, tmp_path: Path) -> None:
        # It would appear to work until the hour was up, then fail on a corpus somebody was
        # half way through re-ingesting.
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        reply = {"access_token": "ya29.x", "scope": READONLY_SCOPE, "expires_in": 3599}

        with pytest.raises(AuthError, match="no refresh token"):
            authorise(secret, receiver=a_receiver(), transport=a_token_transport(reply))

    def test_a_grant_missing_the_scope_is_refused(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        reply = {
            "access_token": "ya29.x",
            "refresh_token": "1//r",
            "scope": "https://www.googleapis.com/auth/userinfo.email",
            "expires_in": 3599,
        }

        with pytest.raises(AuthError, match="does not include"):
            authorise(secret, receiver=a_receiver(), transport=a_token_transport(reply))

    def test_the_secret_goes_to_google_over_a_form_post(self, tmp_path: Path) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        posted: list = []
        authorise(secret, receiver=a_receiver(), transport=a_token_transport(seen=posted))

        assert urllib.parse.urlparse(posted[0]["url"]).hostname in ALLOWED_HOSTS
        assert posted[0]["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert posted[0]["form"]["grant_type"] == "authorization_code"

    def test_a_rejected_client_says_which_thing_google_did_not_recognise(
        self, tmp_path: Path
    ) -> None:
        secret = ClientSecret.load(a_client_secret(tmp_path / "c.json"))
        reply = {"error": "invalid_client", "error_description": "Unauthorized"}

        with pytest.raises(AuthError, match="invalid_client"):
            authorise(
                secret,
                receiver=a_receiver(),
                transport=a_token_transport(reply, status=401),
            )


class TestOneSignInIsNotOneExchangePerRequest:
    """A walk of a large folder is hundreds of requests."""

    def test_a_token_is_minted_once_and_reused(self) -> None:
        clock = [1000.0]
        token = AccessToken(a_credential(), transport=a_token_transport(), clock=lambda: clock[0])

        assert [token() for _ in range(200)] == ["ya29.access"] * 200
        assert token.mints == 1

    def test_it_is_re_minted_before_it_expires_rather_than_after(self) -> None:
        clock = [1000.0]
        token = AccessToken(a_credential(), transport=a_token_transport(), clock=lambda: clock[0])
        token()

        # An hour's token, re-minted with a minute to spare, so a walk that starts with
        # thirty seconds left does not fail half way through a folder.
        clock[0] += 3599 - 61
        token()
        assert token.mints == 1

        clock[0] += 2
        token()
        assert token.mints == 2

    def test_a_revoked_grant_says_to_authorise_again(self) -> None:
        reply = {"error": "invalid_grant", "error_description": "Token has been expired"}
        token = AccessToken(a_credential(), transport=a_token_transport(reply, status=400))

        with pytest.raises(AuthError, match="dramatis authorise"):
            token()

    def test_a_refresh_names_the_grant_it_is_spending(self) -> None:
        posted: list = []
        AccessToken(a_credential(), transport=a_token_transport(seen=posted))()

        assert posted[0]["form"]["grant_type"] == "refresh_token"
        assert posted[0]["form"]["refresh_token"] == "1//refresh"

    def test_a_token_with_no_lifetime_is_not_cached_for_ever(self) -> None:
        clock = [1000.0]
        reply = {"access_token": "ya29.x", "scope": READONLY_SCOPE}
        token = AccessToken(
            a_credential(), transport=a_token_transport(reply), clock=lambda: clock[0]
        )
        token()
        token()

        assert token.mints == 2


def test_the_module_reads_no_environment_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """`credential_path` is a function, not a constant.

    A module-level path frozen at import would ignore an override set afterwards, which is
    how a test suite ends up writing a credential into somebody's real configuration
    directory.
    """
    monkeypatch.setenv("DRAMATIS_GOOGLE_CREDENTIAL", os.path.join("a", "b.json"))

    assert credential_path() == Path("a") / "b.json"

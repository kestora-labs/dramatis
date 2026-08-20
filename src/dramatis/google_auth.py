"""Consenting to read a Google Drive folder, once, and keeping the answer somewhere sane.

**4.13** built a source that takes a bearer token. This obtains one, and it is the whole of
what Dramatis knows about Google accounts.

**An installed-app flow, not a service account.** A service account would mean re-sharing
every folder with a robot address before anything could be read, which is a change to the
user's Drive that a read-only tool has no business asking for. A pasted access token expires
in an hour. So the user brings a client secret they created once, consents in a browser once,
and a refresh token does the rest (**D56**).

**The refresh token is cached outside the project file.** This is the part worth being
careful about: a project store is a thing people send to each other — that is the point of a
single portable file — and a credential must not travel in one. The cache sits in the user's
own configuration directory, is written with owner-only permission where the platform has
any, and nothing in `store` can reach it.

**Read-only scope, and one host for each half of the exchange.** The consent screen asks for
`drive.readonly` and nothing else, so what Dramatis may do with the grant is enforced by
Google rather than promised by Dramatis. The token endpoint is checked against an allowlist
before a client secret is posted to it, because the address comes out of a JSON file the user
downloaded and a mistyped or tampered one would send that secret somewhere else.

**Loopback, not a device code.** The browser comes back to a listener on `127.0.0.1` bound to
a port the operating system chooses, alive for exactly one exchange. `state` is checked, so a
stray request cannot complete somebody's sign-in for them, and PKCE means the authorisation
code is worthless to anything that did not start the flow.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dramatis.drive import READONLY_SCOPE
from dramatis.sources import IngestError

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

ALLOWED_HOSTS = frozenset({"oauth2.googleapis.com", "accounts.google.com"})
"""Where a client secret and an authorisation code may be sent.

Both addresses arrive in a JSON file the user downloaded from Google, and a file is a thing
that can be edited, mistyped, or swapped. Posting a client secret to whatever host that file
names would make the one genuinely dangerous request in this project depend on the contents
of an unvalidated document.
"""

CREDENTIAL_ENV = "DRAMATIS_GOOGLE_CREDENTIAL"
CLIENT_SECRET_ENV = "DRAMATIS_GOOGLE_CLIENT_SECRET"

CREDENTIAL_FILENAME = "google-drive.json"
CREDENTIAL_VERSION = 1

CONSENT_TIMEOUT = 300.0
"""Five minutes to sign in and consent. Long enough to find a password, short enough that a
forgotten terminal does not hold a socket open for the rest of the day."""

EXPIRY_MARGIN = 60.0
"""An access token is re-minted this long before it expires, so a walk that starts with
thirty seconds left on the clock does not fail half way through a folder."""

TokenTransport = Callable[[str, bytes, Mapping[str, str], float], bytes]
"""How the token request is sent: ``(url, payload, headers, timeout) -> body``.

Injected by tests. Nothing in an ordinary test run reaches the real one, because reaching it
would mean contacting Google with somebody's client secret.
"""


class AuthError(IngestError):
    """Consent or a token exchange failed.

    Deliberately an `IngestError`: from a caller's point of view a credential that cannot be
    obtained is one of the reasons a corpus cannot be read, and every command that reads a
    corpus already reports that kind of failure as a sentence rather than a traceback.
    """


# -- what the user brings ----------------------------------------------------------------


@dataclass(frozen=True)
class ClientSecret:
    """The OAuth client a user created in their own Google Cloud project.

    Dramatis ships no client of its own, for the reason it ships no model keys: a client
    identifier published in an open-source repository is a shared secret with the whole
    internet, and the consent screen it drives would name a project the user has no control
    over.
    """

    client_id: str
    client_secret: str
    auth_uri: str = AUTH_URI
    token_uri: str = TOKEN_URI

    @classmethod
    def load(cls, path: Path | str) -> ClientSecret:
        """Read the `client_secret_*.json` Google hands out for a Desktop app."""
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AuthError(f"no client secret at {path}") from None
        except (OSError, json.JSONDecodeError) as error:
            raise AuthError(f"{path} is not a readable client secret: {error}") from None

        if not isinstance(payload, dict):
            raise AuthError(f"{path} is not a client secret document")
        if "web" in payload and "installed" not in payload:
            raise AuthError(
                f"{path} is a Web application client. Dramatis runs on the user's own "
                "machine, so create an OAuth client of type Desktop app and download that."
            )

        inner = payload.get("installed") or payload
        client_id = str(inner.get("client_id") or "")
        client_secret = str(inner.get("client_secret") or "")
        if not client_id or not client_secret:
            raise AuthError(
                f"{path} has no client_id and client_secret. Download the JSON for an OAuth "
                "client of type Desktop app from the Google Cloud console."
            )

        secret = cls(
            client_id=client_id,
            client_secret=client_secret,
            auth_uri=str(inner.get("auth_uri") or AUTH_URI),
            token_uri=str(inner.get("token_uri") or TOKEN_URI),
        )
        _require_allowed(secret.auth_uri, path)
        _require_allowed(secret.token_uri, path)
        return secret


def _require_allowed(uri: str, source: Path | str) -> None:
    """The address, and the scheme it is reached over.

    The scheme is checked as well as the host because `http://oauth2.googleapis.com` passes a
    host check and puts a client secret on the wire in clear. A downloaded file naming it
    would be the whole hole.
    """
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise AuthError(
            f"{source} points at {uri!r}, which is not a Google sign-in address. Dramatis "
            f"will only send a client secret over https to "
            f"{', '.join(sorted(ALLOWED_HOSTS))}."
        )


# -- where the answer is kept ------------------------------------------------------------


@dataclass(frozen=True)
class Credential:
    """A refresh token and the client it belongs to. Never stored in a project."""

    client_id: str
    client_secret: str
    refresh_token: str
    scope: str
    obtained_at: str

    def as_json(self) -> dict[str, Any]:
        return {
            "credential_version": CREDENTIAL_VERSION,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "scope": self.scope,
            "obtained_at": self.obtained_at,
            "note": (
                "A Google refresh token for read-only Drive access. This file is not part of "
                "any Dramatis project and must never be copied into one."
            ),
        }


def config_dir() -> Path:
    """The user's own configuration directory, by the convention of their platform.

    Written out rather than taken from a dependency: three lines of `os.environ` against a
    package whose only job is to know these three lines.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "dramatis"


def credential_path() -> Path:
    """Where the refresh token lives. Never inside a project store, by construction."""
    override = (os.environ.get(CREDENTIAL_ENV) or "").strip()
    return Path(override) if override else config_dir() / CREDENTIAL_FILENAME


def save_credential(credential: Credential, path: Path | None = None) -> Path:
    """Write the credential with owner-only permission, atomically.

    Created through `os.open` with the mode set rather than written and then `chmod`-ed:
    between those two calls the file exists and is readable by everybody on the machine, and
    a credential does not get to have a window like that. On Windows the mode is very nearly
    meaningless — `chmod` there sets a read-only flag and nothing more — so the sentence this
    module can honestly make is *owner-only where the platform has permissions at all*.
    """
    target = Path(path) if path is not None else credential_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(f"{target.name}.{os.getpid()}.pending")

    payload = json.dumps(credential.as_json(), indent=2) + "\n"
    handle = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(payload)
        os.replace(pending, target)
    finally:
        Path(pending).unlink(missing_ok=True)
    return target


def load_credential(path: Path | None = None) -> Credential:
    """The cached credential, or a message saying how to get one."""
    target = Path(path) if path is not None else credential_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AuthError(
            "Dramatis has not been authorised to read Google Drive. Run "
            "`dramatis authorise --client-secret <file>` once; it opens a browser and asks "
            "for read-only access."
        ) from None
    except (OSError, json.JSONDecodeError) as error:
        raise AuthError(f"{target} is not a readable credential: {error}") from None

    missing = [
        key
        for key in ("client_id", "client_secret", "refresh_token")
        if not str(payload.get(key) or "")
    ]
    if missing:
        raise AuthError(f"{target} is missing {', '.join(missing)}. Delete it and authorise again.")

    return Credential(
        client_id=str(payload["client_id"]),
        client_secret=str(payload["client_secret"]),
        refresh_token=str(payload["refresh_token"]),
        scope=str(payload.get("scope") or READONLY_SCOPE),
        obtained_at=str(payload.get("obtained_at") or ""),
    )


def forget_credential(path: Path | None = None) -> bool:
    """Delete the cached credential. Returns whether there was one.

    Local only, and said so at the call site: this stops *this machine* using the grant, and
    does not revoke it at Google. Pretending otherwise would be the worse of the two errors.
    """
    target = Path(path) if path is not None else credential_path()
    if not target.exists():
        return False
    target.unlink()
    return True


# -- the exchange -------------------------------------------------------------------------


def _post(url: str, payload: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    """The real token request. The one place this project sends a secret anywhere."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise AuthError(f"refusing to post a client secret to {url}")
    request = urllib.request.Request(  # noqa: S310 - the host is checked above
        url, data=payload, headers=dict(headers), method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return bytes(response.read())


def _exchange(
    token_uri: str,
    form: Mapping[str, str],
    *,
    transport: TokenTransport | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    _require_allowed(token_uri, "the client secret")
    send = transport or _post
    body = urllib.parse.urlencode(list(form.items())).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        raw = send(token_uri, body, headers, timeout)
    except urllib.error.HTTPError as error:
        raise _token_failure(error) from error
    except urllib.error.URLError as error:
        raise AuthError(f"could not reach Google to exchange a token: {error.reason}") from error

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthError(f"Google returned something that is not JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise AuthError("Google returned something that is not a token response")
    return decoded


def _token_failure(error: urllib.error.HTTPError) -> AuthError:
    detail, code = "", ""
    try:
        body = json.loads(error.read().decode("utf-8", "replace")) or {}
        code = str(body.get("error") or "")
        detail = str(body.get("error_description") or "")
    except Exception:  # pragma: no cover - the body is a courtesy, not a contract
        code, detail = "", ""

    if code == "invalid_grant":
        return AuthError(
            "Google rejected the stored credential (invalid_grant). It was revoked, expired "
            "after long disuse, or the account's password changed. Run `dramatis authorise "
            "--client-secret <file>` again."
        )
    if code == "invalid_client":
        return AuthError(
            "Google did not recognise the OAuth client (invalid_client). The client secret "
            "does not match the project that issued the grant; authorise again with the "
            "client secret you mean to use."
        )
    said = f": {detail}" if detail else ""
    return AuthError(f"Google refused the token request ({error.code} {code or 'error'}){said}")


# -- consent ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Consent:
    """What came back from the browser."""

    code: str
    redirect_uri: str


Receiver = Callable[[str, str, float], Consent]
"""How the authorisation code is collected: ``(auth_url, state, timeout) -> Consent``.

A parameter so that everything about the flow except the socket can be tested without one,
and so a future front end could collect consent its own way without this module growing a
mode switch.
"""


class _Redirect(http.server.BaseHTTPRequestHandler):
    """Answers exactly one useful request and puts what it was asked into `sink`."""

    sink: dict[str, list[str]] = {}

    def do_GET(self) -> None:  # noqa: N802 - the name is BaseHTTPRequestHandler's
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            # A browser asks for /favicon.ico unbidden, and answering that as though it were
            # the redirect would end the flow before the redirect arrived.
            self.send_error(404)
            return

        self.sink.update(query)
        message = (
            "Dramatis is authorised. You can close this tab."
            if "code" in query
            else "Dramatis was not authorised. You can close this tab."
        )
        body = (
            "<!doctype html><meta charset=utf-8><title>Dramatis</title>"
            f"<p style='font:16px/1.5 system-ui;margin:3rem'>{message}</p>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        """Silence. A CLI's output is its contract; a request log is not part of it."""


def _handler_for(sink: dict[str, list[str]]) -> type[_Redirect]:
    """A handler class bound to one flow's answer, rather than to a module-level one."""
    return type("Redirect", (_Redirect,), {"sink": sink})


class LoopbackReceiver:
    """Collects the authorisation code from a browser redirect to `127.0.0.1`.

    Bound to a port the operating system chooses and alive for one exchange: the listener
    exists between opening a browser and the redirect coming back, and not a moment either
    side of that. It is bound to the loopback address rather than to every interface, so
    nothing off this machine can reach it even during that moment.
    """

    def __init__(self, open_browser: Callable[[str], Any] = webbrowser.open) -> None:
        self.open_browser = open_browser

    def __call__(self, auth_url: str, state: str, timeout: float) -> Consent:
        answer: dict[str, list[str]] = {}
        server = http.server.HTTPServer(("127.0.0.1", 0), _handler_for(answer))
        server.timeout = timeout
        redirect_uri = f"http://127.0.0.1:{server.server_port}/"
        url = f"{auth_url}&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"

        try:
            # On stderr, like every other remark this CLI makes. The address is printed as
            # well as opened: a browser cannot be opened over SSH, and a flow that fails
            # silently there would be unusable on exactly the machines corpora live on.
            print("opening a browser to ask for read-only access to Drive.", file=sys.stderr)
            print("if it does not open, visit:", file=sys.stderr)
            print(f"  {url}", file=sys.stderr)
            self.open_browser(url)

            deadline = time.monotonic() + timeout
            while not answer and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        return _consent_from(answer, state, redirect_uri, timeout)


def _consent_from(
    answer: Mapping[str, list[str]], state: str, redirect_uri: str, timeout: float
) -> Consent:
    """What the browser came back with, or the reason it is not usable."""
    if not answer:
        raise AuthError(f"nothing came back from the browser within {timeout:g}s")

    if "error" in answer:
        reason = answer["error"][0]
        if reason == "access_denied":
            raise AuthError("consent was declined in the browser. Nothing was stored.")
        raise AuthError(f"consent failed ({reason}). Nothing was stored.")

    if answer.get("state", [""])[0] != state:
        # Somebody else's redirect, or a forged one. Never exchange a code that did not come
        # back from the request this process started.
        raise AuthError("the browser came back with the wrong state; consent was abandoned")

    return Consent(code=answer["code"][0], redirect_uri=redirect_uri)


def _verifier() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    Without it, an authorisation code visible to anything else on the machine — a loopback
    port is not private to one process — could be exchanged by whoever saw it. With it, the
    code is worthless without a secret that never left this process.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorisation_url(secret: ClientSecret, *, challenge: str, state: str) -> str:
    """The consent address, minus the redirect the receiver has to choose the port for."""
    params = {
        "client_id": secret.client_id,
        "response_type": "code",
        "scope": READONLY_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # Without both of these Google returns an access token and no refresh token on a
        # second authorisation, and the flow appears to work until the hour is up.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{secret.auth_uri}?{urllib.parse.urlencode(list(params.items()))}"


def authorise(
    secret: ClientSecret,
    *,
    receiver: Receiver | None = None,
    transport: TokenTransport | None = None,
    timeout: float = CONSENT_TIMEOUT,
    now: Callable[[], datetime] | None = None,
) -> Credential:
    """Run the consent flow and return the credential it produced. Stores nothing."""
    verifier, challenge = _verifier()
    state = secrets.token_urlsafe(32)
    collect = receiver or LoopbackReceiver()

    consent = collect(authorisation_url(secret, challenge=challenge, state=state), state, timeout)

    payload = _exchange(
        secret.token_uri,
        {
            "grant_type": "authorization_code",
            "code": consent.code,
            "client_id": secret.client_id,
            "client_secret": secret.client_secret,
            "code_verifier": verifier,
            "redirect_uri": consent.redirect_uri,
        },
        transport=transport,
    )

    refresh = str(payload.get("refresh_token") or "")
    if not refresh:
        raise AuthError(
            "Google returned no refresh token, so the grant would expire within the hour. "
            "This happens when the account has already consented; revoke Dramatis's access "
            "at https://myaccount.google.com/permissions and authorise again."
        )

    scope = str(payload.get("scope") or READONLY_SCOPE)
    if READONLY_SCOPE not in scope.split():
        raise AuthError(
            f"the grant does not include {READONLY_SCOPE}, so no folder could be read with "
            f"it. Google returned: {scope!r}"
        )

    stamp = (now() if now else datetime.now(UTC)).isoformat(timespec="seconds")
    return Credential(
        client_id=secret.client_id,
        client_secret=secret.client_secret,
        refresh_token=refresh,
        scope=scope,
        obtained_at=stamp,
    )


# -- using it ------------------------------------------------------------------------------


class AccessToken:
    """Mints an access token from the refresh token, and reuses it until it is nearly spent.

    A `drive.Credentials` callable, so `DriveSource` needs to know none of this. Reused
    rather than re-minted because a walk of a large folder is hundreds of requests, and a
    token exchange per request would turn one sign-in into a rate limit.
    """

    def __init__(
        self,
        credential: Credential,
        *,
        transport: TokenTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.credential = credential
        self.transport = transport
        self.clock = clock
        self._token = ""
        self._expires_at = 0.0
        self.mints = 0
        """How many times a token was actually fetched. Read by tests; harmless elsewhere."""

    def __call__(self) -> str:
        if self._token and self.clock() < self._expires_at:
            return self._token

        payload = _exchange(
            TOKEN_URI,
            {
                "grant_type": "refresh_token",
                "refresh_token": self.credential.refresh_token,
                "client_id": self.credential.client_id,
                "client_secret": self.credential.client_secret,
            },
            transport=self.transport,
        )
        token = str(payload.get("access_token") or "")
        if not token:
            raise AuthError("Google returned no access token for the stored credential")

        try:
            lifetime = float(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            lifetime = 0.0

        self._token = token
        self._expires_at = self.clock() + max(lifetime - EXPIRY_MARGIN, 0.0)
        self.mints += 1
        return token


def drive_credentials(
    path: Path | None = None, *, transport: TokenTransport | None = None
) -> AccessToken:
    """The callable `DriveSource` wants, from whatever is cached on this machine."""
    return AccessToken(load_credential(path), transport=transport)

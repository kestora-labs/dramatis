"""A corpus that lives in a Google Drive folder.

The second implementation of `sources.Source`, and the reason **D56** amended Invariant 7.
Most of the corpora this application is wanted for are Google Docs in Drive folders, and the
only way to analyse one until now was to export the lot by hand — a chore that has to be
repeated on every revision, which quietly breaks the one thing revisions are for.

**It answers the same two questions and nothing else.** A root the corpus is known by, and
its readable documents as `(path, text)` pairs with everything else skipped *and named*.
Nothing downstream knows this exists: hashing, document identity, revisions, structure maps
and region exclusion all work on pairs, which is what **4.12** established and what this
bullet is the first test of.

**A Google Doc is exported as Markdown**, which keeps the headings that structure inference
reads, and lands on `.md`, already a text suffix everywhere else in the project. A file
somebody uploaded is downloaded as it stands and judged by its suffix — the same rule a
folder uses, so `notes.md` is read and `cover.png` is skipped with the same sentence in both
places. Identity is unchanged: **D32**'s hash is taken over the exported text, so an edited
Doc becomes a new document and a new revision exactly as an edited file does.

**Constructing a source contacts nothing.** Invariant 7 permits reaching a named source
*while ingesting* and never otherwise, so every request in this module happens inside `read`.
There is exactly one host, every request is a GET, and both are checked rather than promised:
`_send` refuses any other host and any other method, so a bug here cannot become egress
somewhere else.

**No SDK, and no new dependency.** Drive speaks JSON over HTTPS and the standard library
gets JSON over HTTPS. `google-api-python-client` would pull in a large transitive tree to
save a hundred lines of `urlencode`, and Invariant 7 is a claim about where bytes go: the
smaller the transport, the cheaper it is for somebody to check the claim. The same reasoning
picked `urllib` for Ollama (**D44**).

**Authentication is not here.** This takes a bearer token from its caller and never obtains
one. The OAuth installed-app flow, the refresh token cached outside every project, and the
CLI that reaches a network only when a run names a Drive source are `google_auth` and
``dramatis authorise`` (**4.14**).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from dramatis.sources import TEXT_SUFFIXES, IngestError, Reading
from dramatis.text import normalise_line_endings

API = "https://www.googleapis.com/drive/v3"
HOST = "www.googleapis.com"

READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
"""The only scope this needs. Invariant 7 says a named source is read-only; a scope is where
that stops being a promise and becomes something Google enforces on Dramatis's behalf."""

FOLDER_MIME = "application/vnd.google-apps.folder"
DOCUMENT_MIME = "application/vnd.google-apps.document"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
GOOGLE_MIME_PREFIX = "application/vnd.google-apps."

EXPORT_MIME = "text/markdown"
"""What a Google Doc is exported as.

Markdown rather than plain text because the headings survive it, and headings are what
structure inference reads to propose where a narrative begins. Rather than HTML because the
text a quotation is verified against must be the text a person would recognise, and Invariant
3 rejects an extraction whose quotation is not found verbatim — every tag between them is a
verification failure waiting to happen.
"""

MARKDOWN_SUFFIXES = (".md", ".markdown")

PAGE_SIZE = "1000"
DEFAULT_TIMEOUT = 120.0
"""Long enough for a large Doc to be rendered to Markdown server-side, which is slower than
fetching bytes that already exist."""

FOLDER_ID = re.compile(r"^[A-Za-z0-9_-]+$")
"""What a Drive identifier may contain.

Checked rather than trusted, because the identifier is interpolated into the `q` query
Drive parses. A quote in an id would be a query-injection in a request Dramatis composes.
"""

Transport = Callable[[str, str, Mapping[str, str], float], bytes]
"""How a request is sent: ``(method, url, headers, timeout) -> body``.

Injected by tests so nothing here needs a Drive, an account, or a network. The headers are a
parameter rather than built inside because the bearer token belongs to the caller, and a
recorded-traffic transport wants to see what was actually sent.
"""

Credentials = str | Callable[[], str]
"""A bearer access token, or something that produces one when asked.

The callable form is what **4.14** hands over: a refresh token cached outside the project
file, exchanged for an access token at the moment of use rather than held for the life of a
process. Taking either shape now means 4.14 adds a credential and changes nothing here.
"""


def folder_id(named: str) -> str:
    """The folder identifier, from whatever a person pasted.

    The Drive parallel of `Path.resolve()`, and load-bearing for the same reason: the root is
    the key a confirmed structure map is saved under, so a browser URL, a bare id and the
    `gdrive:` form a stored project reports back must all reduce to one answer. Otherwise a
    person who confirmed a structure map from a pasted URL would be asked again for the same
    folder the next time they pasted the id.
    """
    value = (named or "").strip()
    if not value:
        raise IngestError("no Drive folder was named")

    if value.startswith("gdrive:folder/"):
        value = value[len("gdrive:folder/") :]
    elif "://" in value:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname not in ("drive.google.com", "docs.google.com"):
            raise IngestError(f"{named!r} is not a Google Drive address")
        parts = [part for part in parsed.path.split("/") if part]
        if "folders" in parts:
            value = parts[parts.index("folders") + 1] if parts[-1] != "folders" else ""
        else:
            raise IngestError(
                f"{named!r} does not name a Drive folder. A folder's address looks like "
                "https://drive.google.com/drive/folders/<id>."
            )

    if not FOLDER_ID.match(value):
        raise IngestError(
            f"{named!r} is not a Drive folder identifier. Paste the folder's address from "
            "the browser, or the identifier at the end of it."
        )
    return value


def root_of(identifier: str) -> str:
    """The root string a Drive corpus is known by.

    Prefixed, so a structure map's root says which kind of source it came from and can never
    be mistaken for a path somebody once had on a laptop.
    """
    return f"gdrive:folder/{identifier}"


def _send(method: str, url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    """The real transport. One host, one verb, both refused rather than assumed.

    Invariant 7 is a claim about where bytes go. A check here costs nothing and turns the
    claim into something a reader can confirm without following every call site.
    """
    if method != "GET":
        raise IngestError(f"the Drive source issues no {method} requests; this is a bug")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != HOST:
        # The scheme as well as the host: `http://www.googleapis.com` passes a host check and
        # puts a bearer token on the wire in clear.
        raise IngestError(f"the Drive source contacts https://{HOST} and nothing else; a bug")

    request = urllib.request.Request(url, headers=dict(headers), method=method)  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return bytes(response.read())


@dataclass(frozen=True)
class Entry:
    """One thing found in the tree, before anything has been read."""

    identifier: str
    name: str
    mime_type: str
    path: str
    """Relative to the folder ingested, in the same coordinate space a folder's paths use."""


@dataclass(frozen=True)
class DriveSource:
    """A Drive folder tree, as a corpus.

    ``folder`` is anything `folder_id` accepts: a browser address, a bare identifier, or the
    `gdrive:folder/<id>` form a project reports back.
    """

    folder: str
    credentials: Credentials = ""
    transport: Transport | None = None
    timeout: float = DEFAULT_TIMEOUT
    identifier: str = field(init=False)

    def __post_init__(self) -> None:
        # Parsed at construction because a typo should be a message rather than a request,
        # and because nothing else here may touch the network before `read`.
        object.__setattr__(self, "identifier", folder_id(self.folder))

    @property
    def root(self) -> str:
        return root_of(self.identifier)

    # -- the interface ------------------------------------------------------------------

    def read(self) -> Reading:
        """Walk the tree and read every document in it. The only method that reaches out.

        A document that cannot be read is skipped and named, exactly as a folder's non-text
        files are; a *corpus* that cannot be read raises, because a revision built from the
        half of a corpus that happened to answer is worse than no revision at all. The whole
        tree is walked before any document is read, for that reason.
        """
        label = self._require_folder()

        # Sorted before anything is read, and by identifier as well as path. The order decides
        # the revision hash and the order Drive returns pages in is not a promise — and where
        # two documents land on one path, which of them survives must not depend on it either.
        entries = sorted(self._walk(), key=lambda entry: (entry.path, entry.identifier))

        documents: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []
        seen: set[str] = set()

        for entry in entries:
            if entry.path in seen:
                # Drive lets two things in one folder share a name, and an exported Doc named
                # `notes` collides with an uploaded `notes.md`. Downstream keys documents by
                # path — roles, previous revisions, structure maps — so two documents at one
                # path would silently become one. Named rather than merged.
                skipped.append(
                    (entry.path, f"a second document is already at this path ({entry.name!r})")
                )
                continue
            seen.add(entry.path)

            try:
                documents.append((entry.path, self._read_one(entry)))
            except IngestError as error:
                skipped.append((entry.path, str(error)))

        skipped.sort(key=lambda pair: pair[0])
        return Reading(documents=tuple(documents), skipped=tuple(skipped), label=label or None)

    # -- walking ------------------------------------------------------------------------

    def _require_folder(self) -> str:
        """Fail on a folder that is not there, rather than returning an empty corpus.

        `files.list` answers a query about a nonexistent parent with an empty list, so without
        this a mistyped identifier reads as a folder holding nothing — and the message a user
        would get is about their corpus being empty rather than about their typo.

        Returns the folder's name, which the same reply already carries. It becomes the
        `Reading`'s label and so the default title of the work, because an identifier is a
        poor name for somebody's novel and this costs no second request.
        """
        payload = self._json(f"/files/{self.identifier}", {"fields": "id,name,mimeType"})
        if payload.get("mimeType") != FOLDER_MIME:
            raise IngestError(
                f"{self.root} is not a folder; it is {payload.get('mimeType', 'something else')}. "
                "Name the folder holding the corpus."
            )
        return str(payload.get("name") or "")

    def _walk(self) -> Iterable[Entry]:
        """Every document under the root, folders expanded in place."""
        pending: list[tuple[str, str]] = [(self.identifier, "")]
        visited: set[str] = {self.identifier}

        while pending:
            parent, prefix = pending.pop(0)
            for child in self._children(parent):
                name = str(child.get("name") or "")
                mime = str(child.get("mimeType") or "")
                identifier = str(child.get("id") or "")

                if not FOLDER_ID.match(identifier):
                    # An identifier is interpolated into the path of the next request. Drive
                    # has never returned anything but this alphabet; a check costs nothing and
                    # means a surprise cannot become a request somewhere else.
                    continue

                if mime == FOLDER_MIME:
                    if identifier in visited:
                        # A folder may sit under two parents, and a tree that revisits one
                        # walks for ever.
                        continue
                    visited.add(identifier)
                    pending.append((identifier, f"{prefix}{name}/"))
                    continue

                yield Entry(
                    identifier=identifier,
                    name=name,
                    mime_type=mime,
                    path=f"{prefix}{_document_path(name, mime)}",
                )

    def _children(self, parent: str) -> Iterable[dict[str, object]]:
        """One folder's contents, following `nextPageToken` to the end."""
        token = ""
        while True:
            params = {
                "q": f"'{parent}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType)",
                "pageSize": PAGE_SIZE,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token

            payload = self._json("/files", params)
            files = payload.get("files")
            yield from (files if isinstance(files, list) else [])

            token = str(payload.get("nextPageToken") or "")
            if not token:
                return

    # -- reading one document -----------------------------------------------------------

    def _read_one(self, entry: Entry) -> str:
        """The text of one document, or an `IngestError` saying why there is none.

        Raising rather than returning a reason keeps the two cases — a Doc that exports and a
        Doc that does not — in the shape `read` already handles for everything else.
        """
        if entry.mime_type == DOCUMENT_MIME:
            raw = self._bytes(
                f"/files/{entry.identifier}/export",
                {"mimeType": EXPORT_MIME, "supportsAllDrives": "true"},
            )
            return _as_text(entry.name, raw)

        if entry.mime_type == SHORTCUT_MIME:
            raise IngestError(
                "a shortcut, which Dramatis does not follow. Put the document itself in the "
                "folder, or name the folder it really lives in."
            )

        if entry.mime_type.startswith(GOOGLE_MIME_PREFIX):
            kind = entry.mime_type[len(GOOGLE_MIME_PREFIX) :]
            raise IngestError(f"a Google {kind}, which has no text to read")

        # An uploaded file, judged by its suffix — the rule a folder uses, so the same file
        # is read or skipped for the same stated reason wherever it is kept.
        suffix = _suffix_of(entry.name)
        if suffix not in TEXT_SUFFIXES:
            raise IngestError(f"not a text file ({suffix or 'no suffix'})")

        raw = self._bytes(
            f"/files/{entry.identifier}", {"alt": "media", "supportsAllDrives": "true"}
        )
        return _as_text(entry.name, raw)

    # -- the wire -----------------------------------------------------------------------

    def _token(self) -> str:
        credentials = self.credentials
        token = credentials() if callable(credentials) else credentials
        if not token:
            raise IngestError(
                "no Google credential was given, so the Drive folder cannot be read. "
                "Authorise Dramatis for read-only Drive access first."
            )
        return token

    def _url(self, path: str, params: Mapping[str, str]) -> str:
        # Built from an ordered mapping so one request is always one URL: a recorded exchange
        # is matched by its address, and a query whose parameters shuffled would miss.
        return f"{API}{path}?{urllib.parse.urlencode(list(params.items()))}"

    def _bytes(self, path: str, params: Mapping[str, str]) -> bytes:
        send = self.transport or _send
        url = self._url(path, params)
        headers = {"Authorization": f"Bearer {self._token()}", "Accept-Encoding": "identity"}
        try:
            return send("GET", url, headers, self.timeout)
        except urllib.error.HTTPError as error:
            raise _from_status(error, path) from error
        except urllib.error.URLError as error:
            raise IngestError(f"could not reach Google Drive: {error.reason}") from error
        except TimeoutError as error:
            raise IngestError(
                f"Google Drive did not answer within {self.timeout:g}s. A very large document "
                "can take longer to export than to download; raise the timeout."
            ) from error

    def _json(self, path: str, params: Mapping[str, str]) -> dict[str, object]:
        raw = self._bytes(path, params)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IngestError(
                f"Google Drive returned something that is not JSON: {error}"
            ) from error
        if not isinstance(decoded, dict):
            raise IngestError(f"Google Drive returned {type(decoded).__name__}, expected an object")
        return decoded


# -- helpers ----------------------------------------------------------------------------


def _suffix_of(name: str) -> str:
    _, dot, suffix = name.rpartition(".")
    return f".{suffix.lower()}" if dot else ""


def _document_path(name: str, mime_type: str) -> str:
    """What a thing in Drive is called once it is a document.

    A Google Doc has no suffix — Drive keeps the type out of band — so the exported document
    takes `.md`, which is what it now is and what makes every suffix rule downstream apply to
    it unchanged. A Doc somebody named `notes.txt` becomes `notes.txt.md` rather than keeping
    a suffix that would misdescribe its contents.
    """
    if mime_type != DOCUMENT_MIME:
        return name
    return name if name.lower().endswith(MARKDOWN_SUFFIXES) else f"{name}.md"


def _as_text(name: str, raw: bytes) -> str:
    """Bytes from Drive as text, by the rules `sources.read_text` applies to a file.

    Same decoding, same normalisation, same refusal to guess an encoding. The text stored and
    hashed has to be the text every locator and quotation is later resolved against, and that
    cannot depend on where the document was kept.
    """
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestError(
            f"{name} is not valid UTF-8 (byte {error.start}). Dramatis does not guess "
            "encodings, because guessing wrong corrupts quotations silently."
        ) from None

    text = normalise_line_endings(decoded)
    if not text.strip():
        raise IngestError(f"{name} is empty")
    return text


def _from_status(error: urllib.error.HTTPError, path: str) -> IngestError:
    """A Drive error status as a sentence somebody can act on.

    The reason each of these is spelled out is that they are the ones a real corpus produces:
    a token that expired mid-walk, a folder shared with somebody else's account, a Doc too
    large to export, and a folder large enough to be rate-limited while being read.
    """
    detail, reasons = "", set()
    try:
        inner = (json.loads(error.read().decode("utf-8", "replace")) or {}).get("error") or {}
        detail = str(inner.get("message") or "")
        # Drive puts the prose in `message` and the machine-readable cause in `errors[].reason`.
        # Reading only the first is how `exportSizeLimitExceeded` came back as a bare "refused
        # access", which sends somebody to check their sharing settings over a size limit.
        reasons = {str(entry.get("reason") or "") for entry in inner.get("errors") or []}
    except Exception:  # pragma: no cover - the body is a courtesy, not a contract
        detail, reasons = "", set()

    if error.code == 401:
        return IngestError(
            "Google Drive rejected the credential (401). It has expired or been revoked; "
            "authorise Dramatis again."
        )
    if error.code == 403 and "exportSizeLimitExceeded" in reasons:
        return IngestError("too large for Drive to export (10MB); it was not read")
    if error.code == 403:
        said = f": {detail}" if detail else ""
        return IngestError(
            f"Google Drive refused access (403){said}. The account that authorised Dramatis "
            "may not have access to this folder."
        )
    if error.code == 404:
        return IngestError(f"Google Drive has nothing at {path} (404). Check the folder address.")
    if error.code == 429:
        return IngestError(
            "Google Drive is rate-limiting this account (429). Wait and ingest again; nothing "
            "was stored."
        )
    if error.code >= 500:
        return IngestError(f"Google Drive is unavailable ({error.code}). Try again later.")
    said = f": {detail}" if detail else ""
    return IngestError(f"Google Drive rejected the request ({error.code}){said}")

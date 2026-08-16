"""Reading a text into a store as a hashed revision.

Ingest is deliberately dull and deliberately idempotent. Ingesting the same file twice
produces the same revision identifier and leaves the store unchanged, because the identifier
is derived from the content hash. That is what lets a user re-ingest a folder without
wondering whether they have just created a duplicate of everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dramatis import ids
from dramatis.store import Document, Store, TextRevision, utc_now
from dramatis.text import content_hash, normalise_line_endings, revision_hash

DEFAULT_ROLE = "narrative"


class IngestError(Exception):
    """A file could not be ingested. The message is meant for a user, not a traceback."""


@dataclass(frozen=True)
class IngestResult:
    collection_id: str
    work_id: str
    document_id: str
    revision_id: str
    sha256: str
    characters: int
    already_present: bool

    @property
    def summary(self) -> str:
        state = "already present" if self.already_present else "ingested"
        # ASCII only: a Windows console under a legacy code page renders a typographic
        # ellipsis as a replacement character, which looks like corruption in the one line
        # a user reads most often.
        return (
            f"{state}: {self.revision_id} ({self.characters:,} characters, "
            f"sha256 {self.sha256[:12]}...)"
        )


def read_text(path: Path) -> str:
    """Read a file as UTF-8 and normalise its line endings.

    The normalised form is what gets stored and hashed. Storing the raw bytes and hashing a
    normalised copy would mean the recorded hash described something other than the text on
    which every locator and quotation is resolved.
    """
    # Checked before reading rather than caught after: a directory read raises
    # IsADirectoryError on POSIX and PermissionError on Windows, and the distinction is
    # not worth carrying into the error message a user sees.
    if path.is_dir():
        raise IngestError(f"{path} is a directory; ingesting folders arrives in phase 4")

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise IngestError(f"no such file: {path}") from None
    except OSError as error:
        raise IngestError(f"cannot read {path}: {error.strerror}") from None

    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise IngestError(
            f"{path} is not valid UTF-8 (byte {error.start}). Convert it first; Dramatis "
            "does not guess encodings, because guessing wrong corrupts quotations silently."
        ) from None

    text = normalise_line_endings(decoded)
    if not text.strip():
        raise IngestError(f"{path} is empty")
    return text


def ingest_file(
    store: Store,
    path: Path,
    *,
    work_title: str | None = None,
    collection_name: str | None = None,
    creator: str | None = None,
    language: str | None = None,
    label: str | None = None,
    role: str = DEFAULT_ROLE,
    now: str | None = None,
) -> IngestResult:
    """Ingest one plain-text file as a work with a single-document text revision."""
    if role not in {"narrative", "reference"}:
        raise IngestError(f"unknown document role {role!r}; expected 'narrative' or 'reference'")

    path = Path(path)
    text = read_text(path)

    title = work_title or path.stem.replace("_", " ").replace("-", " ").strip() or path.name
    collection = collection_name or title

    work_id = ids.work_id(title)
    document_id = ids.document_id(path.stem or path.name)

    existing_work = store.get_work(work_id)
    existing_collections = store.list_collections()

    if existing_work is not None and collection_name is None:
        # A work keeps the collection it is already in unless the caller names one.
        # Without this, re-ingesting without repeating --collection would mint a second,
        # empty collection from the default and report the work as belonging to it — untrue,
        # and quietly corrupting, since the collection scopes the character registry.
        collection_id = str(existing_work["collection_id"])
    elif collection_name is None and len(existing_collections) == 1:
        # A new work joins the collection the store already holds. This is what makes a
        # shared universe work: two novels ingested into one project share one registry,
        # so a character appearing in both is one character.
        collection_id = str(existing_collections[0]["id"])
    else:
        collection_id = ids.collection_id(collection)
        others = [entry for entry in existing_collections if entry["id"] != collection_id]
        if others:
            names = ", ".join(f"{entry['name']!r}" for entry in others)
            raise IngestError(
                f"this project already holds the collection {names}, and a project holds "
                f"one collection. The registry is collection-scoped, so putting {collection!r} "
                "here would merge two casts into one namespace. Use a separate project file "
                "for it, or pass the existing collection's name to add a work to it."
            )
        store.upsert_collection(collection_id, collection)

    document_sha = content_hash(text)
    revision_sha = revision_hash([text])
    revision_id = ids.revision_id(revision_sha)

    already_present = store.get_text_revision(revision_id) is not None

    store.upsert_work(
        work_id,
        collection_id,
        title,
        creator=creator,
        language=language,
        segment_types=[],
    )
    store.upsert_document(
        Document(
            id=document_id,
            work_id=work_id,
            title=title,
            path=path.name,
            role=role,
            media_type="text/plain",
            sha256=document_sha,
            content=text,
        )
    )
    store.upsert_text_revision(
        TextRevision(
            id=revision_id,
            work_id=work_id,
            label=label,
            sha256=revision_sha,
            created_at=now or utc_now(),
            document_ids=(document_id,),
        )
    )

    return IngestResult(
        collection_id=collection_id,
        work_id=work_id,
        document_id=document_id,
        revision_id=revision_id,
        sha256=revision_sha,
        characters=len(text),
        already_present=already_present,
    )

"""Reading a text into a store as a hashed revision.

Ingest is deliberately dull and deliberately idempotent. Ingesting the same file twice
produces the same revision identifier and leaves the store unchanged, because the identifier
is derived from the content hash. That is what lets a user re-ingest a folder without
wondering whether they have just created a duplicate of everything.

**A revision may hold many documents.** A novelist's draft is a folder of chapter files, so
`ingest_folder` takes the folder as it stands and makes one revision of it. It infers no
convention: it does not decide that `draft-2/` is a revision of `draft-1/`, or that a file
called `cast.md` is reference material. The folder pointed at is the revision, and which
folder that is remains the user's sentence to say. Fixture **B** states its own
directory-per-revision layout as data precisely so that no code has to know it.

**A document is a path and the content that was at it.** Document identifiers carry both
(see `ids.document_id`), so an edited file becomes a new row rather than overwriting the one
an older revision depends on, and two files that happen to hold identical bytes stay two
documents. What makes `chapter-03.md` the same chapter in two drafts is that it sits at the
same path relative to the folder ingested, and that is what per-file tracking compares.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dramatis import ids
from dramatis.store import (
    COLLECTIVES_ARE_ACTORS,
    DOCUMENT_ROLES,
    EXCLUDED,
    NARRATIVE,
    Document,
    Store,
    TextRevision,
    utc_now,
)
from dramatis.text import content_hash, normalise_line_endings, revision_hash

DEFAULT_ROLE = NARRATIVE


def _locate_raw(text: str, quotation: str) -> tuple[int, int] | None:
    """Where a verbatim quotation falls in raw text, tolerant of whitespace runs.

    The structure map records a region's boundary as a quotation the model returned; the file
    on disk may have been reflowed since. A pattern built from the quotation — each run of
    whitespace matching any run — finds it either way, and returns *raw* offsets, so the slice
    lands in the same coordinate space as the text it cuts. That is the point: a stored offset
    would be in normalised space, and applying it to raw text lands wrong by however much the
    file was hard-wrapped, which is the class of bug 2.4 and 3.1 spent effort on.
    """
    stripped = quotation.strip()
    if not stripped:
        return None
    pattern = r"\s+".join(re.escape(token) for token in re.split(r"\s+", stripped))
    match = re.search(pattern, text)
    return (match.start(), match.end()) if match else None


def kept_text(text: str, plan: Mapping[str, Any]) -> tuple[str, str | None]:
    """A document's text with any region marked ``excluded`` removed (**4.11**).

    Returns the kept text and, when a boundary cannot be located, a note saying nothing was
    dropped and why — never a silent guess, because cutting at the wrong place removes a
    chapter from the analysis with nothing on screen to say so.

    The kept span is the narrative region, found by its verbatim boundary quotations: front
    matter before it goes when a region *before* it is excluded, an appendix after it goes
    when a region *after* it is excluded. The offsets the map records are the hint; the
    quotations are the authority — the rule `text` states of every offset in this project.
    """
    regions = list(plan.get("regions") or [])
    excluded = [i for i, region in enumerate(regions) if _role_of(region) == EXCLUDED]
    if not excluded:
        return text, None

    narrative = next(
        ((i, r) for i, r in enumerate(regions) if r.get("begins_with") or r.get("ends_with")),
        None,
    )
    if narrative is None:
        return (
            text,
            "a region is marked excluded but none carries a boundary to cut at; kept whole",
        )
    index, region = narrative
    begins, ends = region.get("begins_with") or "", region.get("ends_with") or ""

    start, end = 0, len(text)
    if any(i < index for i in excluded) and begins:
        found = _locate_raw(text, begins)
        if found is None:
            return (
                text,
                f"the narrative start is not in the document; kept whole: {begins[:40]!r}",
            )
        start = found[0]
    if any(i > index for i in excluded) and ends:
        found = _locate_raw(text, ends)
        if found is None:
            return (
                text,
                f"the narrative end is not in the document; kept whole: {ends[:40]!r}",
            )
        end = found[1]

    kept = text[start:end]
    if not kept.strip():
        return text, "excluding the region would empty the document; kept whole"
    return kept, None


def _role_of(region: Mapping[str, Any]) -> Any:
    return (region.get("role") or {}).get("value")


TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
"""Which files in a folder are taken as text.

A draft folder holds more than the draft — notes in other formats, an exported PDF, the
editor's own dotfiles. Reading everything would put binary into a revision hash; guessing
by sniffing content would be the encoding guess `read_text` already refuses. So the rule is
the suffix, and anything else is reported as skipped rather than passed over in silence.
"""


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
    excluded: bool = False
    """True when a confirmed region was dropped from the document before storing (**4.11**)."""

    @property
    def summary(self) -> str:
        state = "already present" if self.already_present else "ingested"
        # ASCII only: a Windows console under a legacy code page renders a typographic
        # ellipsis as a replacement character, which looks like corruption in the one line
        # a user reads most often.
        excluded = "\n  a confirmed region was excluded" if self.excluded else ""
        return (
            f"{state}: {self.revision_id} ({self.characters:,} characters, "
            f"sha256 {self.sha256[:12]}...)" + excluded
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
        raise IngestError(f"{path} is a directory; use ingest_folder")

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


def _resolve_collection(store: Store, work_id: str, collection_name: str | None, title: str) -> str:
    """Decide which collection a work being ingested belongs to.

    Shared by both ingest paths so a folder and a file answer this the same way. The rules
    are unchanged; only their home is.
    """
    collection = collection_name or title
    existing_work = store.get_work(work_id)
    existing_collections = store.list_collections()

    if existing_work is not None and collection_name is None:
        # A work keeps the collection it is already in unless the caller names one.
        # Without this, re-ingesting without repeating --collection would mint a second,
        # empty collection from the default and report the work as belonging to it — untrue,
        # and quietly corrupting, since the collection scopes the character registry.
        return str(existing_work["collection_id"])

    if collection_name is None and len(existing_collections) == 1:
        # A new work joins the collection the store already holds. This is what makes a
        # shared universe work: two novels ingested into one project share one registry,
        # so a character appearing in both is one character.
        return str(existing_collections[0]["id"])

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
    return collection_id


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
    collectives_are_actors: bool | None = None,
) -> IngestResult:
    """Ingest one plain-text file as a work with a single-document text revision.

    ``collectives_are_actors`` records the terms of the study on a project that has not
    recorded them yet (D19). Passing it to a project that already has an answer overwrites
    that answer; callers that mean to change it should say so to the person first, because
    it makes existing snapshots incomparable with everything after.
    """
    if role not in DOCUMENT_ROLES:
        raise IngestError(f"unknown document role {role!r}; expected 'narrative' or 'reference'")

    if collectives_are_actors is not None:
        store.set_setting(COLLECTIVES_ARE_ACTORS, bool(collectives_are_actors))

    path = Path(path)
    text = read_text(path)

    # A single file is its own structure-map root (structure.propose_structure), keyed under
    # its resolved path with one document named for the file. Absent a confirmed map this is
    # empty and nothing is dropped; where a preface region was confirmed excluded, its text
    # never enters the store, so it never reaches extraction (4.11).
    plan = store.structure_map(str(path.resolve())).get(path.name, {})
    kept, exclusion_note = kept_text(text, plan)
    if exclusion_note:
        # A confirmed exclusion that cannot be applied is refused, not ignored: keeping the
        # preface silently would produce exactly the polluted cast the exclusion was for.
        raise IngestError(f"{path.name}: {exclusion_note}")
    excluded = kept != text
    text = kept

    title = work_title or path.stem.replace("_", " ").replace("-", " ").strip() or path.name

    work_id = ids.work_id(title)
    collection_id = _resolve_collection(store, work_id, collection_name, title)

    document_sha = content_hash(text)
    # Identified by the same path it is stored under, so identity and the `path` column
    # cannot disagree about where the document is.
    document_id = ids.document_id(path.name, document_sha)
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
        excluded=excluded,
    )


@dataclass(frozen=True)
class FileOutcome:
    """One file in an ingested folder, and how it stands against the previous revision."""

    path: str
    """Relative to the folder ingested, so the same chapter in two drafts has one path."""
    document_id: str
    sha256: str
    characters: int
    state: str
    """``added``, ``changed`` or ``unchanged``."""


@dataclass(frozen=True)
class FolderIngestResult:
    collection_id: str
    work_id: str
    revision_id: str
    sha256: str
    documents: tuple[FileOutcome, ...]
    skipped: tuple[tuple[str, str], ...]
    """(path, why), so a file left out of the revision is a thing the user is told."""
    already_present: bool
    compared_with: str | None
    """The revision this one was measured against, or None if it is the first."""
    confirmed: tuple[str, ...] = ()
    """Documents whose role came from a confirmed structure map rather than from ``role``.

    Reported because it is the difference between a folder somebody has classified and one
    that took a flag's default, and nothing else on screen distinguishes them.
    """
    excluded: tuple[str, ...] = ()
    """Documents a confirmed region was dropped from before storing (**4.11**)."""

    @property
    def characters(self) -> int:
        return sum(document.characters for document in self.documents)

    def of_state(self, state: str) -> tuple[FileOutcome, ...]:
        return tuple(entry for entry in self.documents if entry.state == state)

    @property
    def summary(self) -> str:
        state = "already present" if self.already_present else "ingested"
        counts = ", ".join(
            f"{len(self.of_state(name))} {name}"
            for name in ("added", "changed", "unchanged")
            if self.of_state(name)
        )
        confirmed = (
            f"\n  {len(self.confirmed)} took the role you confirmed for this folder"
            if self.confirmed
            else ""
        )
        excluded = (
            f"\n  {len(self.excluded)} had a confirmed region excluded" if self.excluded else ""
        )
        return (
            f"{state}: {self.revision_id} ({len(self.documents)} documents, "
            f"{self.characters:,} characters, sha256 {self.sha256[:12]}...)"
            + (f"\n  {counts}" if counts else "")
            + confirmed
            + excluded
        )


def _text_files(root: Path) -> list[Path]:
    """Every text file under ``root``, in a deterministic order.

    Sorted by relative path, because the order decides the revision hash: two ingests of one
    folder must produce the same revision, and a filesystem's own ordering is not a promise.
    """
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _previous_state(store: Store, work_id: str) -> tuple[str | None, dict[str, str]]:
    """The newest revision of this work, as a map of path to content hash."""
    revisions = store.list_text_revisions(work_id)
    if not revisions:
        return None, {}

    latest = revisions[-1]
    by_path: dict[str, str] = {}
    for document_id in latest.document_ids:
        document = store.get_document(document_id)
        if document is not None and document.path:
            by_path[document.path] = document.sha256
    return latest.id, by_path


def ingest_folder(
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
    collectives_are_actors: bool | None = None,
) -> FolderIngestResult:
    """Ingest a folder of text files as one text revision of one work.

    Every readable text file under ``path`` becomes a document of the revision, ordered by
    its path. Files that are not text, and files with nothing in them, are skipped and named
    in the result rather than dropped quietly.

    Each file is reported as ``added``, ``changed`` or ``unchanged`` against the newest
    existing revision of the work — which is what makes a later diff able to say that a
    graph moved because one chapter was rewritten and not because the analysis changed.

    ``role`` applies to files this folder has no confirmed answer for. Where a structure map
    has been confirmed (**4.2**), each document takes the role somebody gave it, because a
    single role for a whole folder cannot describe fixture **C**, which keeps its reference
    material and its narrative side by side.
    """
    if role not in DOCUMENT_ROLES:
        raise IngestError(f"unknown document role {role!r}; expected 'narrative' or 'reference'")

    path = Path(path)
    if not path.exists():
        raise IngestError(f"no such folder: {path}")
    if not path.is_dir():
        raise IngestError(f"{path} is a file; use ingest_file")

    if collectives_are_actors is not None:
        store.set_setting(COLLECTIVES_ARE_ACTORS, bool(collectives_are_actors))

    title = work_title or path.name.replace("_", " ").replace("-", " ").strip() or path.name
    work_id = ids.work_id(title)
    collection_id = _resolve_collection(store, work_id, collection_name, title)

    readable: list[tuple[str, str]] = []  # (relative path, text)
    skipped: list[tuple[str, str]] = []

    for found in _text_files(path):
        relative = found.relative_to(path).as_posix()
        if found.suffix.lower() not in TEXT_SUFFIXES:
            skipped.append((relative, f"not a text file ({found.suffix or 'no suffix'})"))
            continue
        try:
            readable.append((relative, read_text(found)))
        except IngestError as error:
            # One unreadable file should not discard a folder the user meant to ingest, but
            # it must be visible: a revision quietly missing a chapter is a graph missing a
            # character, with nothing on screen to say why.
            skipped.append((relative, str(error)))

    if not readable:
        raise IngestError(
            f"{path} holds no readable text files. Expected one of "
            f"{', '.join(sorted(TEXT_SUFFIXES))}."
        )

    # The shape read here is what `structure.as_json` writes. Read through the store rather
    # than by importing that module, which imports this one.
    plans = store.structure_map(str(path.resolve()))
    confirmed = {
        relative: (entry.get("role") or {}).get("value") for relative, entry in plans.items()
    }
    roles = {relative: confirmed.get(relative) or role for relative, _ in readable}

    # A confirmed excluded region — a preface bound into a chapter file — is dropped before
    # the text is hashed or stored, so it never reaches extraction (4.11). Refused, not
    # ignored, when its boundary cannot be found, for the reason `ingest_file` gives.
    excluded_paths: list[str] = []
    kept_readable: list[tuple[str, str]] = []
    for relative, document_text in readable:
        kept, exclusion_note = kept_text(document_text, plans.get(relative, {}))
        if exclusion_note:
            raise IngestError(f"{relative}: {exclusion_note}")
        if kept != document_text:
            excluded_paths.append(relative)
        kept_readable.append((relative, kept))
    readable = kept_readable

    previous_id, previous = _previous_state(store, work_id)

    revision_sha = revision_hash([text for _, text in readable])
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

    outcomes: list[FileOutcome] = []
    document_ids: list[str] = []

    for relative, text in readable:
        document_sha = content_hash(text)
        # The relative path, not the stem: `draft-1/chapter-01.md` and `draft-2/chapter-01.md`
        # are two documents even when they are byte-identical, which is the usual state of a
        # chapter nobody touched between drafts. See D40.
        document_id = ids.document_id(relative, document_sha)
        store.upsert_document(
            Document(
                id=document_id,
                work_id=work_id,
                title=Path(relative).stem.replace("_", " ").replace("-", " "),
                path=relative,
                role=roles[relative],
                media_type="text/markdown"
                if relative.endswith((".md", ".markdown"))
                else "text/plain",
                sha256=document_sha,
                content=text,
            )
        )
        document_ids.append(document_id)

        was = previous.get(relative)
        outcomes.append(
            FileOutcome(
                path=relative,
                document_id=document_id,
                sha256=document_sha,
                characters=len(text),
                state="added" if was is None else "unchanged" if was == document_sha else "changed",
            )
        )

    store.upsert_text_revision(
        TextRevision(
            id=revision_id,
            work_id=work_id,
            label=label,
            sha256=revision_sha,
            created_at=now or utc_now(),
            document_ids=tuple(document_ids),
        )
    )

    return FolderIngestResult(
        collection_id=collection_id,
        work_id=work_id,
        revision_id=revision_id,
        sha256=revision_sha,
        documents=tuple(outcomes),
        skipped=tuple(skipped),
        already_present=already_present,
        compared_with=previous_id,
        confirmed=tuple(sorted(relative for relative in roles if relative in confirmed)),
        excluded=tuple(sorted(excluded_paths)),
    )

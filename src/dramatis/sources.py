"""Where a corpus comes from, as an interface with two questions.

Everything Dramatis does to a corpus is defined on a list of `(path, text)` pairs. Content
hashing, document identity, revisions, structure maps, region exclusion — none of them ever
touches a filesystem; they touch that list, and a string naming where it came from. Until
now a directory walk was the only thing that could produce one, and the walk was written
twice, inline, in `ingest` and in `structure`.

A **source** is that seam named. It answers two questions and nothing else:

**What is the stable root this corpus is known by?** A string. It is the key a confirmed
structure map is saved under, so it has to be the same string every time somebody points at
the same corpus by a different route — which for the filesystem means resolving `corpus`,
`./corpus` and `../work/corpus` to one answer, and elsewhere means whatever plays that part
there.

**What are its readable documents?** `(path, text)` pairs in a deterministic order, because
the order decides the revision hash, together with the things it could not read and *why*. A
source never drops a document in silence: a revision quietly missing a chapter is a graph
missing a character, with nothing on screen to say so.

`FileSystemSource` is the first implementation and, until **4.13**, the only one. It is not
privileged. Nothing downstream may ask a source for a `Path`, because the next source has
none to give.

**A source is contacted only when it is read.** For a folder that distinction is invisible.
For a source that reaches a network it is Invariant 7's whole guarantee — constructing one
names it, reading one contacts it — so `read` is a method rather than something computed in
`__init__`, and the `Reading` it returns is a value the caller keeps and passes on rather
than a thing to go back and ask for twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from dramatis.text import normalise_line_endings

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
"""Which files in a folder are taken as text.

A draft folder holds more than the draft — notes in other formats, an exported PDF, the
editor's own dotfiles. Reading everything would put binary into a revision hash; guessing
by sniffing content would be the encoding guess `read_text` already refuses. So the rule is
the suffix, and anything else is reported as skipped rather than passed over in silence.
"""


class IngestError(Exception):
    """A corpus, or one document of it, could not be read. The message is for a user.

    It lives here rather than in `ingest` because a source raises it before `ingest` is
    involved. `ingest` re-exports it, so every caller that already imports it from there is
    unaffected and there is still only one exception type on this path.
    """


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


@dataclass(frozen=True)
class Reading:
    """One reading of a source: what it holds, and what it could not read.

    This is the whole of what a source hands downstream. Anything not in here is not
    available later, which is the rule that keeps a network source from being contacted
    twice for one ingest — and keeps the local case honest about the same thing.
    """

    documents: tuple[tuple[str, str], ...]
    """(path, text), in the order that decides the revision hash."""

    skipped: tuple[tuple[str, str], ...] = ()
    """(path, why), so a document left out of the revision is a thing the user is told."""

    @property
    def texts(self) -> dict[str, str]:
        """The documents keyed by path, for the callers that want them that way."""
        return dict(self.documents)


@runtime_checkable
class Source(Protocol):
    """A corpus, wherever it lives."""

    @property
    def root(self) -> str:
        """The stable identifier this corpus is known by, however it was named."""

    def read(self) -> Reading:
        """Read it now. Raises `IngestError` where the corpus itself cannot be reached."""


@dataclass(frozen=True)
class FileSystemSource:
    """A folder, a folder tree, or a single file on this machine.

    A single file is a corpus of one — the shape **4.9** offers as an equal, and the one a
    public-domain novel with a critical introduction bound into it arrives as. It is its own
    root, a path distinct from any folder's, holding one document named for the file. Which
    is why a document's path is a *relative* string in both cases and never the path read
    from: `novel.txt` names the document whether it came out of a folder or was the corpus
    entire.
    """

    path: Path | str

    @property
    def root(self) -> str:
        # Resolved, because the root is the key a confirmed map is saved under: `corpus` and
        # `./corpus` and the same folder reached from a parent directory are one folder, and
        # a user who moved between them would otherwise be asked the same questions again.
        return str(Path(self.path).resolve())

    def read(self) -> Reading:
        root = Path(self.root)
        if not root.exists():
            raise IngestError(f"no such file or folder: {root}")

        if root.is_dir():
            # Sorted by relative path, because the order decides the revision hash: two
            # ingests of one folder must produce the same revision, and a filesystem's own
            # ordering is not a promise.
            found = sorted(
                (
                    (candidate.relative_to(root).as_posix(), candidate)
                    for candidate in root.rglob("*")
                    if candidate.is_file()
                ),
                key=lambda entry: entry[0],
            )
        else:
            found = [(root.name, root)]

        documents: list[tuple[str, str]] = []
        skipped: list[tuple[str, str]] = []
        for relative, candidate in found:
            if candidate.suffix.lower() not in TEXT_SUFFIXES:
                skipped.append((relative, f"not a text file ({candidate.suffix or 'no suffix'})"))
                continue
            try:
                documents.append((relative, read_text(candidate)))
            except IngestError as error:
                # One unreadable file should not discard a corpus the user meant to read,
                # but it must be visible.
                skipped.append((relative, str(error)))

        return Reading(documents=tuple(documents), skipped=tuple(skipped))


def as_source(corpus: Source | Path | str) -> Source:
    """A source, from either a source or a path.

    Every function that used to take a path now takes either, and a path means the local
    filesystem. Written once so that no caller acquires its own opinion about what a bare
    path means.
    """
    if isinstance(corpus, (str, Path)):
        return FileSystemSource(corpus)
    return corpus

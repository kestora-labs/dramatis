"""Identifier construction.

Identifiers are deterministic, not random. Phase 3 requires that re-running an identical
analysis over identical text produces an identical graph, and that is impossible if every
ingest mints fresh UUIDs. Deriving identifiers from content and names instead means the
same input always yields the same identifiers, so two snapshots can be diffed meaningfully.

Identifiers are namespaced by kind (``work:``, ``doc:``, ``rev:``) so that a bare string is
self-describing in a stored document.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_SEPARATORS = re.compile(r"[\s_/\\]+")
_UNSAFE = re.compile(r"[^a-z0-9-]+")
_RUNS = re.compile(r"-{2,}")

MAX_SLUG_LENGTH = 80


def slugify(value: str) -> str:
    """Reduce arbitrary text to a lowercase, hyphenated, ASCII-safe token.

    Accented characters are decomposed to their base form rather than dropped, so
    ``Zoë`` becomes ``zoe`` rather than ``zo``.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = _SEPARATORS.sub("-", ascii_only.strip().lower())
    cleaned = _RUNS.sub("-", _UNSAFE.sub("-", lowered)).strip("-")
    return cleaned[:MAX_SLUG_LENGTH].strip("-")


def _identifier(kind: str, value: str, fallback: str) -> str:
    slug = slugify(value) or fallback
    return f"{kind}:{slug}"


def collection_id(name: str) -> str:
    return _identifier("col", name, "untitled")


def work_id(title: str, edition: str | None = None) -> str:
    """Identify a work, and where there is one, the edition of it (**6.4**).

    **An edition is part of a work's identity, and a revision is not.** Fixture **D** puts it
    plainly: *"An edition must not be modelled as the newest text revision of a work. Both
    must remain addressable."* Two drafts of a novel are one work moving forward and the
    later supersedes the earlier; the 1889 and 1903 texts are both authoritative, both
    citable, and both current, and a reader may legitimately want the graph of the 1889 text
    specifically. Sharing one identifier would make the second ingest a revision of the
    first, and the question *who is in the 1889 text?* would stop having an answer.

    **A work with no edition keeps the bare form**, exactly as `relation_id` leaves
    ``observed`` unsuffixed: every identifier already written down stays where it is, and a
    project that never had an edition never grows one.
    """
    base = _identifier("work", title, "untitled")
    if not edition:
        return base
    return f"{base}@{slugify(edition) or 'edition'}"


def work_edition(identifier: str) -> tuple[str, str | None]:
    """Read a work identifier back into the work and the edition it names.

    The inverse of `work_id`, and it lives here for the reason `relation_endpoints` gives:
    the ``@`` join is this module's convention, and a caller taking it apart itself would be
    treating a convention as a rule. Returns ``(identifier, None)`` for a work with no
    edition, so a caller can compare bases without checking first.
    """
    base, separator, edition = identifier.partition("@")
    return (base, edition) if separator and edition else (identifier, None)


DOCUMENT_HASH_LENGTH = 12


def document_id(path: str, content_sha: str) -> str:
    """Identify one version of one file: where it sits, and what it held there.

    **Both halves are required, because neither identifies a document alone.**

    Without the content, a second ingest of an edited file lands on the same identifier and
    overwrites the text an earlier revision points at — the older revision then reports text
    it never contained, and its recorded hash stops matching what it returns. Nothing raises;
    the graph, its evidence, and every quotation anchored into that revision simply describe
    a text that no longer exists. That is the defect **D32** fixed.

    Without the path, two files holding the same bytes become one document. That is not an
    exotic case but the ordinary one: a drafts folder is mostly chapters nobody touched
    between revisions, so ingesting the folder that contains both drafts asks a single
    document to sit at two places in one revision. See **D40**.

    ``path`` is the one the document is stored under — relative to the folder ingested, not
    absolute. Relative is what lets a file untouched between two drafts keep its identifier,
    so the two revisions share one row; an absolute path would mint a new document every time
    the folder moved.

    The slug is for a human tracing an evidence locator back to a file. It is not what makes
    the identifier unique: `slugify` collapses separators and truncates at
    `MAX_SLUG_LENGTH`, so two genuinely different paths can reduce to one token. The hash
    therefore covers the path as well as the content, and uniqueness never rests on the slug
    being lossless.

    Idempotence is unaffected: the same bytes at the same path always yield the same
    identifier, which is the property `ingest` promises.
    """
    digest = hashlib.sha256(f"{path}\0{content_sha}".encode()).hexdigest()
    return f"doc:{slugify(path) or 'untitled'}-{digest[:DOCUMENT_HASH_LENGTH]}"


def character_id(name: str, *, disambiguator: str | None = None) -> str:
    """Derive a character identifier from a canonical name.

    Two different characters can share a name, so a disambiguator may be appended. Once a
    character is in the registry its identifier never changes even if a later run would
    prefer a different canonical name — the registry, not this function, is what makes
    identity stable across snapshots.
    """
    slug = slugify(name) or "unnamed"
    if disambiguator:
        slug = f"{slug}-{slugify(disambiguator)}"
    return f"char:{slug}"


def relation_id(first: str, second: str, provenance: str = "observed") -> str:
    """Derive an identifier for the relation between two characters.

    The endpoints are sorted, so the same pair yields the same identifier whichever way
    round it was observed. An undirected edge that changed identity depending on which
    character the model happened to name first would defeat diffing entirely.

    **Provenance is part of the identity**, and only ``observed`` is unsuffixed. A pair the
    reference material declares and the narrative never enacts is the finding fixture **C**
    exists for — *"a pipeline that merges the two provenance classes into one graph loses
    both findings"* — and one identifier for both would be exactly that merge. ``observed``
    keeps the bare form so identifiers already written down do not move (**4.3**).
    """
    left, right = sorted((first, second))
    stem = f"rel:{left.removeprefix('char:')}--{right.removeprefix('char:')}"
    return stem if provenance == "observed" else f"{stem}@{provenance}"


def relation_endpoints(identifier: str) -> tuple[str, str, str] | None:
    """Read a relation identifier back into the endpoints and provenance it was built from.

    The inverse of `relation_id`, and it lives here for that reason: the `--` join and the
    `@provenance` suffix are this module's conventions, and a caller that took them apart
    itself would be treating a convention as a rule. Returns None for anything not built here.

    The split is unambiguous because `slugify` collapses runs of hyphens, so no character slug
    can contain `--`.

    Needed by **5.3**: merging two characters changes which pair an edge joins, and a review or
    a correction recorded against the old pair has to be able to follow it.
    """
    if not identifier.startswith("rel:"):
        return None
    stem, _, provenance = identifier.removeprefix("rel:").partition("@")
    left, separator, right = stem.partition("--")
    if not separator or not left or not right:
        return None
    return f"char:{left}", f"char:{right}", provenance or "observed"


def revision_id(content_hash: str) -> str:
    """Derive a revision identifier from the hash of its content.

    The same text always produces the same revision identifier, in this store and in any
    other. That is what makes two independently produced snapshots comparable.
    """
    return f"rev:{content_hash[:12]}"

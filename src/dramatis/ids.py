"""Identifier construction.

Identifiers are deterministic, not random. Phase 3 requires that re-running an identical
analysis over identical text produces an identical graph, and that is impossible if every
ingest mints fresh UUIDs. Deriving identifiers from content and names instead means the
same input always yields the same identifiers, so two snapshots can be diffed meaningfully.

Identifiers are namespaced by kind (``work:``, ``doc:``, ``rev:``) so that a bare string is
self-describing in a stored document.
"""

from __future__ import annotations

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


def work_id(title: str) -> str:
    return _identifier("work", title, "untitled")


DOCUMENT_HASH_LENGTH = 12


def document_id(name: str, content_sha: str | None = None) -> str:
    """Identify a document, optionally by the content it holds.

    **A document row is one version of a file, not the file itself.** Without the hash, a
    second ingest of an edited file lands on the same identifier and overwrites the content
    an earlier revision points at — the older revision then reports text it never contained,
    and its recorded hash stops matching what it returns. Nothing raises; the graph, its
    evidence, and every quotation anchored into that revision simply describe a text that no
    longer exists.

    Including the hash makes each version its own row, so a revision keeps the exact text it
    was hashed from. What survives across versions is the *path*, which is where a file's
    identity actually lives: `chapter-03.md` is the same chapter in two drafts however much
    of it was rewritten.

    Idempotence is unaffected and slightly improved — the same bytes always yield the same
    identifier, which is the property `ingest` promises.
    """
    slug = slugify(name) or "untitled"
    if content_sha:
        slug = f"{slug}-{content_sha[:DOCUMENT_HASH_LENGTH]}"
    return f"doc:{slug}"


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


def relation_id(first: str, second: str) -> str:
    """Derive an identifier for the relation between two characters.

    The endpoints are sorted, so the same pair yields the same identifier whichever way
    round it was observed. An undirected edge that changed identity depending on which
    character the model happened to name first would defeat diffing entirely.
    """
    left, right = sorted((first, second))
    return f"rel:{left.removeprefix('char:')}--{right.removeprefix('char:')}"


def revision_id(content_hash: str) -> str:
    """Derive a revision identifier from the hash of its content.

    The same text always produces the same revision identifier, in this store and in any
    other. That is what makes two independently produced snapshots comparable.
    """
    return f"rev:{content_hash[:12]}"

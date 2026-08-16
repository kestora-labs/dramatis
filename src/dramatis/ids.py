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


def document_id(name: str) -> str:
    return _identifier("doc", name, "untitled")


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

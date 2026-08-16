"""Text normalisation and quotation lookup.

Invariant 3 requires every evidence quotation to be found verbatim in the source. Taken
literally that would fail on almost every real quotation, because source texts are
hard-wrapped and a quoted sentence spans line breaks that are an artefact of layout rather
than part of the work.

"Verbatim" is therefore defined against whitespace-normalised text on both sides: runs of
whitespace collapse to a single space. Nothing else is altered — no case folding, no
punctuation or quotation-mark substitution, no unicode folding. A quotation that differs
from the source in any character other than the width of a gap is a failure, and is
supposed to be.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_WHITESPACE = re.compile(r"\s+")


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to a single space and strip the ends.

    For quotation matching only. This is lossy and must never be used for hashing — two
    genuinely different texts can normalise to the same string.
    """
    return _WHITESPACE.sub(" ", text).strip()


def normalise_line_endings(text: str) -> str:
    """Convert CRLF and CR to LF, and drop a leading byte-order mark.

    For hashing. A text checked out on Windows and the same text on Linux must produce the
    same hash, or a revision identifier would depend on who ingested it. Nothing else is
    altered: indentation, blank lines, and trailing spaces are all preserved, because they
    are part of the text and a change to them is a real change.
    """
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def content_hash(text: str) -> str:
    """Return the SHA-256 of a single text, after line-ending normalisation."""
    return hashlib.sha256(normalise_line_endings(text).encode("utf-8")).hexdigest()


def revision_hash(contents: Iterable[str]) -> str:
    """Return the SHA-256 of a revision made of one or more documents.

    Defined as the hash of the normalised contents concatenated in document order, which is
    what the schema says a text revision's hash is. A single-document revision therefore
    hashes identically to its one document — the common case, and the least surprising
    result. Reordering documents changes the hash, because it changes the work.
    """
    digest = hashlib.sha256()
    for content in contents:
        digest.update(normalise_line_endings(content).encode("utf-8"))
    return digest.hexdigest()


def find_quotation(source: str, quotation: str) -> int | None:
    """Return the offset of ``quotation`` within ``source``, or None if absent.

    Both are whitespace-normalised before matching, so the offset indexes the normalised
    source rather than the original. Offsets are a hint for fast lookup; the quotation and
    its surrounding context are the authority.
    """
    needle = normalise_whitespace(quotation)
    if not needle:
        return None
    offset = normalise_whitespace(source).find(needle)
    return None if offset < 0 else offset


def contains_quotation(source: str, quotation: str) -> bool:
    """Whether ``quotation`` appears in ``source``, ignoring differences in whitespace."""
    return find_quotation(source, quotation) is not None

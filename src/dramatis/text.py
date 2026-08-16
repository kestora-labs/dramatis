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

import re

_WHITESPACE = re.compile(r"\s+")


def normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to a single space and strip the ends."""
    return _WHITESPACE.sub(" ", text).strip()


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

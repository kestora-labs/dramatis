"""Finding a quotation again after the text around it has been edited.

A snapshot records evidence as a W3C TextQuoteSelector — the quotation itself, plus a little
of the text on either side — and never as a character offset, because an offset is invalidated
by an edit anywhere earlier in the file. This module is what makes that choice pay: given a
selector and a text, it says where the quotation is *now*.

The ladder has three rungs, tried in order, and each one is a weaker claim than the one
above it.

**Exact.** The quotation is present verbatim, whitespace-normalised. This survives the
commonest edit by a wide margin: inserting a paragraph before a quoted passage moves every
offset after it and changes nothing about the string being searched for.

**Context.** The quotation is present more than once. That is not a failure — "In vain have
I struggled." may recur — and the surrounding text decides which occurrence was meant. Where
there is no context to decide with, the ambiguity is reported rather than resolved by
picking the first and hoping.

**Fuzzy.** The quotation is no longer present, because the edit was *inside* it. The closest
passage is offered with the similarity that earned it, and below a threshold nothing is
offered at all. This rung is the one that can be wrong, so it never presents itself as
anything else: a caller is told which rung answered, and the client says so on screen.

**What this module will not do is guess quietly.** A quotation whose text has been rewritten
past recognition has no anchor, and saying so is the useful answer. An evidence list that
silently re-pointed at whatever passage scored highest would make every citation in the
project unfalsifiable, which is the opposite of what Invariant 3 is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from dramatis.text import normalise_whitespace

_WHITESPACE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    """Collapse runs of whitespace without stripping the ends.

    `normalise_whitespace` strips, which is right for a quotation and wrong for the context
    around one: a stored prefix ends with the space that joins it to the quotation, and a
    suffix begins with one. Stripping them means comparing "…conditions than" against text
    reading "…conditions than " and disagreeing on the first character tried, which scores
    every occurrence zero and throws away the only thing that could tell them apart.
    """
    return _WHITESPACE.sub(" ", text)


MIN_SIMILARITY = 0.8
"""How close a fuzzy candidate must be before it is offered at all.

Not tuned — chosen as the point below which a "match" is usually a different sentence that
happens to share vocabulary. A wrong passage shown confidently is worse than an admitted
failure, because the reader has no way to discover it; the failure is at least visible and
sends them to the text.
"""

CONTEXT_CHARACTERS = 32
"""How much of the stored prefix and suffix to weigh when occurrences must be told apart.

Enough to separate two occurrences of a repeated line, short enough that an edit just
outside the quotation does not disqualify the occurrence it belongs to.
"""

MAX_CANDIDATES = 200
"""A ceiling on fuzzy candidates examined, so a short quotation in a long book stays cheap."""

FRAGMENT_CHARACTERS = 24
"""The longest fragment of the quotation used to find candidate positions.

Fragments shrink for a short quotation. At a fixed length they would each span most of a
short one, so a single edited word inside it leaves no fragment intact and the passage is
lost for being short rather than for being changed.
"""

MIN_FRAGMENT_CHARACTERS = 12
"""The shortest such fragment. Below this a fragment matches too much of a long work to
narrow anything down."""

FRAGMENTS = 6
"""How many such fragments to take, spread evenly through the quotation.

Not one at each end, which was the first attempt and is not enough: an edit near the front
destroys the leading fragment, and a quotation short enough that its trailing fragment
overlaps the same edit leaves nothing to search for at all — the passage becomes
unrecoverable because of where the edit fell rather than how large it was. Sampling across
the whole quotation means any untouched stretch of it can still find the way home.
"""


@dataclass(frozen=True)
class Anchor:
    """Where a quotation was found, and how much that finding is worth."""

    start: int
    end: int
    method: str
    """``exact``, ``context`` or ``fuzzy`` — which rung of the ladder answered."""
    similarity: float = 1.0
    ambiguous: bool = False
    """True when the text offered several equally good positions and one was taken."""

    @property
    def certain(self) -> bool:
        """Whether the quotation was found verbatim and without a coin toss."""
        return self.method in ("exact", "context") and not self.ambiguous


def occurrences(haystack: str, needle: str, limit: int = MAX_CANDIDATES) -> list[int]:
    """Every offset at which ``needle`` occurs, overlapping matches included."""
    if not needle:
        return []
    found: list[int] = []
    start = 0
    while len(found) < limit and (index := haystack.find(needle, start)) != -1:
        found.append(index)
        start = index + 1
    return found


def _context_score(text: str, start: int, end: int, prefix: str, suffix: str) -> int:
    """How much of the stored surroundings still sit around this occurrence.

    Counted as characters that agree, working outwards from the quotation in both
    directions and stopping at the first disagreement. An edit further out therefore costs
    an occurrence only the part beyond the edit, rather than disqualifying it.
    """
    score = 0

    wanted = prefix[-CONTEXT_CHARACTERS:] if prefix else ""
    have = text[max(0, start - len(wanted)) : start]
    # Not strict: at the start of the text there is less to compare against than was
    # stored, and running out is a short score rather than an error.
    for mine, theirs in zip(reversed(have), reversed(wanted), strict=False):
        if mine != theirs:
            break
        score += 1

    wanted = suffix[:CONTEXT_CHARACTERS] if suffix else ""
    have = text[end : end + len(wanted)]
    for mine, theirs in zip(have, wanted, strict=False):
        if mine != theirs:
            break
        score += 1

    return score


def _by_context(
    text: str, places: list[int], exact: str, prefix: str, suffix: str, hint: int | None
) -> Anchor:
    """Choose among several verbatim occurrences."""
    scored = [
        (_context_score(text, place, place + len(exact), prefix, suffix), place) for place in places
    ]
    best = max(score for score, _ in scored)
    leaders = [place for score, place in scored if score == best]

    if len(leaders) == 1:
        return Anchor(leaders[0], leaders[0] + len(exact), method="context")

    # Nothing in the stored context separates them. The offset hint is the last thing left,
    # and it is a hint — so whichever occurrence is taken, the tie is reported.
    chosen = min(leaders, key=lambda place: abs(place - hint)) if hint is not None else leaders[0]
    return Anchor(
        chosen,
        chosen + len(exact),
        method="context" if best > 0 else "exact",
        ambiguous=True,
    )


def _candidates(text: str, exact: str, prefix: str, suffix: str, hint: int | None) -> list[int]:
    """Places worth measuring a fuzzy match against.

    Generated from the parts of the selector that may have survived: the context on either
    side, and the head and tail of the quotation itself. Searching every offset in the work
    would be both slow and no more accurate — a candidate with none of these near it is not
    the passage being looked for.
    """
    starts: set[int] = set()
    length = len(exact)

    tail = prefix[-CONTEXT_CHARACTERS:] if prefix else ""
    if tail:
        starts.update(place + len(tail) for place in occurrences(text, tail))

    head = suffix[:CONTEXT_CHARACTERS] if suffix else ""
    if head:
        starts.update(max(0, place - length) for place in occurrences(text, head))

    # Fragments spread through the quotation. Each occurrence of a fragment implies where
    # the quotation would begin if that fragment is the part that survived the edit.
    size = min(FRAGMENT_CHARACTERS, max(MIN_FRAGMENT_CHARACTERS, length // 3))
    step = max((length - size) // max(FRAGMENTS - 1, 1), 1)
    for offset in range(0, max(length - size, 0) + 1, step):
        fragment = exact[offset : offset + size]
        if not fragment:
            continue
        starts.update(max(0, place - offset) for place in occurrences(text, fragment))

    if hint is not None and 0 <= hint < len(text):
        starts.add(hint)

    places = [place for place in starts if 0 <= place < len(text)]
    if len(places) > MAX_CANDIDATES:
        # Trimming by position would keep only candidates near the front of the work, which
        # is an arbitrary preference for the opening chapters. The stored offset is the only
        # signal about where to look, so it decides what survives the cut.
        origin = hint if hint is not None else 0
        places = sorted(places, key=lambda place: abs(place - origin))[:MAX_CANDIDATES]

    return sorted(places)


def _trim(window: str, exact: str, offset: int) -> tuple[int, int]:
    """Tighten a candidate window onto the part that actually matches.

    A window is opened at a candidate position and is longer than it needs to be. Marking
    the whole of it would highlight text either side of the quotation as though it were part
    of it.
    """
    blocks = [
        block for block in SequenceMatcher(None, window, exact).get_matching_blocks() if block.size
    ]
    if not blocks:
        return offset, offset + len(window)
    return offset + blocks[0].a, offset + blocks[-1].a + blocks[-1].size


def _fuzzy(text: str, exact: str, prefix: str, suffix: str, hint: int | None) -> Anchor | None:
    """The closest passage to ``exact``, or nothing close enough.

    A candidate position is where the quotation might *begin*, but a word inserted near the
    front moves that beginning, so each window is opened with slack on both sides and then
    trimmed onto the part that actually matches. The similarity is measured on the trimmed
    span rather than on the window, because the trimmed span is what would be highlighted,
    and scoring a window padded with unrelated text would reject good matches for the
    padding this function added itself.
    """
    length = len(exact)
    slack = max(len(exact) // 4, FRAGMENT_CHARACTERS)
    best: tuple[float, int, int] | None = None

    for start in _candidates(text, exact, prefix, suffix, hint):
        opens = max(0, start - slack)
        window = text[opens : start + length + slack]
        if not window:
            continue

        marked = _trim(window, exact, opens)
        similarity = SequenceMatcher(None, text[marked[0] : marked[1]], exact).ratio()
        if best is None or similarity > best[0]:
            best = (similarity, marked[0], marked[1])

    if best is None or best[0] < MIN_SIMILARITY:
        return None

    similarity, opens, closes = best
    return Anchor(opens, closes, method="fuzzy", similarity=round(similarity, 4))


def reanchor(
    text: str,
    exact: str,
    *,
    prefix: str = "",
    suffix: str = "",
    hint: int | None = None,
) -> Anchor | None:
    """Find ``exact`` in ``text``, using its stored surroundings when it has moved.

    ``text`` is expected whitespace-normalised, and the returned offsets index it. Returns
    None when nothing close enough is there, which is a real answer: the passage was cut, or
    rewritten past recognition.
    """
    needle = normalise_whitespace(exact)
    if not needle or not text:
        return None

    prefix = _collapse(prefix)
    suffix = _collapse(suffix)

    places = occurrences(text, needle)
    if len(places) == 1:
        return Anchor(places[0], places[0] + len(needle), method="exact")
    if places:
        return _by_context(text, places, needle, prefix, suffix, hint)

    return _fuzzy(text, needle, prefix, suffix, hint)


def reanchor_selector(text: str, selector: dict) -> Anchor | None:
    """``reanchor`` against a stored selector, as the schema shapes it."""
    return reanchor(
        text,
        selector.get("exact", ""),
        prefix=selector.get("prefix", "") or "",
        suffix=selector.get("suffix", "") or "",
        hint=selector.get("start"),
    )

"""Opening the source text at the position a piece of evidence names.

The graph says two characters are close, the panel says a relation is worth forty-six
passages, and the evidence list names one of them. This is the last step: the passage
itself, with the quotation located inside it.

**The offsets are computed here rather than in the client, and that is the whole point.**
Invariant 3 defines "verbatim" against whitespace-normalised text — runs of whitespace
collapse, nothing else is altered — and `dramatis.text` is where that definition lives. A
browser searching the raw passage for the stored quotation would fail on every quotation
that crosses a hard-wrapped line, which is most of them in a plain-text novel. Reimplementing
the normalisation in TypeScript would put a second copy of Invariant 3 in the tree, and the
copy that drifts is the one nobody is testing. So the server returns text and a span, and
the client only draws.

**A quotation is attributed to the passage it begins in.** `verification` says so, and
allows a span running across a paragraph break to be evidence for the passage where it
starts. So a quotation is not always contained by the passage it names, and the window
widens forward until it holds the whole thing. This is rare and real: 1 of the 1,022
quotations in the first full-novel run is a title page whose lines are separate blocks.

**A quotation that has moved is looked for again.** `open_evidence` tries the recorded
position first — which is right whenever a snapshot is opened against its own revision, and
is a substring search over one passage — and falls back to `reanchor` across the whole work
when the text has been edited under it. What comes back says which rung of that ladder
answered and where the evidence used to point, because a fuzzy match presented identically
to a verbatim one is a citation the reader cannot weigh.

**A quotation that cannot be found at all is reported, not papered over.** The span comes
back empty and the passage is still returned, because showing the right passage without a
highlight is more use than showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dramatis.reanchor import Anchor, reanchor_selector
from dramatis.segmentation import (
    DEFAULT_SEGMENT_TYPE,
    AddressableText,
    Segmentation,
    SegmentationSpec,
)
from dramatis.text import normalise_whitespace


class PassageNotFound(Exception):
    """A locator named a position the text revision does not contain."""


class StructureNotReproducible(Exception):
    """The revision cannot be divided the way the snapshot was divided."""


def spec_for_types(segment_types: list[str] | None) -> SegmentationSpec:
    """The segmentation a work's declared types imply, or a refusal.

    A work records the *names* of its segment types and never the rules that found them. So
    a structure can be reproduced only where the name is enough to identify the rule, and
    exactly one is: the blank-line default, which a work either never overrode or overrode
    with the default's own type.

    **Any other name is refused, including a single one.** The temptation is to treat one
    declared type as "flat, therefore reproducible" and divide on blank lines under that
    name. That is the worst available answer. A work declaring ``chapter`` would have its
    blank-line blocks *called* chapters, and opening "chapter 3" would show the third
    paragraph of the title page with every appearance of having worked. A refusal costs a
    feature; a confident wrong passage costs the reader their trust in every other one.

    Phase 4's structure map is where segmentation rules acquire somewhere to live. Until
    they are stored, this can only reproduce the structure it can derive.
    """
    types = list(segment_types or [])
    if not types or types == [DEFAULT_SEGMENT_TYPE]:
        return SegmentationSpec.flat()

    declared = " › ".join(types)
    raise StructureNotReproducible(
        f"this work is divided into {declared}, and the rules that produced that division "
        "are not stored, so a passage cannot be reopened at the position the snapshot "
        "recorded. Dividing on blank lines instead would open a different passage under "
        "the same name."
    )


@dataclass(frozen=True)
class Passage:
    """A stretch of source text, and where a quotation sits inside it.

    ``text`` is whitespace-normalised, because that is the text the offsets index and the
    text the quotation was verified against. Handing back the raw span and a span measured
    against the normalised one would be two coordinate systems in one object.
    """

    path: list[dict[str, Any]]
    text: str
    start: int | None = None
    end: int | None = None
    document_id: str | None = None
    widened: bool = False
    """True when the window had to grow past the named passage to hold the quotation."""

    method: str = "exact"
    """Which rung of the re-anchoring ladder found it: ``exact``, ``context`` or ``fuzzy``."""
    similarity: float = 1.0
    ambiguous: bool = False
    stored_path: list[dict[str, Any]] | None = None
    """The position the evidence recorded, when that is not where it was found."""

    @property
    def located(self) -> bool:
        return self.start is not None

    @property
    def moved(self) -> bool:
        """Whether the quotation is somewhere other than the position it recorded."""
        return self.stored_path is not None and self.stored_path != self.path


def _position_of(segmentation: Segmentation, path: list[dict[str, Any]]) -> int:
    """The segment a locator path names.

    Matched against ``locator_path`` rather than by walking the parent chain by hand, so
    there is one definition of what a path means and it is the one that produced them.
    Only the indices are compared: a stored path carries the label a segment had when it
    was written, and a label edited since should not make the passage unreachable.
    """
    wanted = [step.get("index") for step in path]
    if not wanted or any(index is None for index in wanted):
        raise PassageNotFound("the locator has no ordinal position to open")

    for position in range(len(segmentation.segments)):
        found = [step.get("index") for step in segmentation.locator_path(position)]
        if found == wanted:
            return position

    readable = " › ".join(f"{step.get('type', '?')} {step.get('index')}" for step in path)
    raise PassageNotFound(f"this revision has no {readable}")


def _following_leaves(segmentation: Segmentation, position: int) -> list[int]:
    """Leaf segments that begin after the named one ends, in order."""
    end = segmentation.segments[position].end
    return [leaf for leaf in segmentation.leaves() if segmentation.segments[leaf].start >= end]


def find_passage(
    segmentation: Segmentation,
    path: list[dict[str, Any]],
    quotation: str = "",
    *,
    document_id: str | None = None,
) -> Passage:
    """Open the passage ``path`` names and locate ``quotation`` within it.

    Raises PassageNotFound if the revision has no such position. A quotation that cannot be
    found yields a passage with no span rather than an error.
    """
    position = _position_of(segmentation, path)
    segment = segmentation.segments[position]
    text = normalise_whitespace(segmentation.text[segment.start : segment.end])
    needle = normalise_whitespace(quotation)

    if not needle:
        return Passage(path=path, text=text, document_id=document_id)

    offset = text.find(needle)
    if offset >= 0:
        return Passage(
            path=path,
            text=text,
            start=offset,
            end=offset + len(needle),
            document_id=document_id,
        )

    # Not contained here, so this is the passage the quotation *begins* in. Grow forward,
    # one passage at a time, and stop as soon as enough text has been added to hold the
    # rest of it — if it were there, it would have been found by then.
    widened = text
    for leaf in _following_leaves(segmentation, position):
        following = segmentation.segments[leaf]
        addition = normalise_whitespace(segmentation.text[following.start : following.end])
        if not addition:
            continue
        widened = f"{widened} {addition}"

        offset = widened.find(needle)
        if offset >= 0:
            return Passage(
                path=path,
                text=widened,
                start=offset,
                end=offset + len(needle),
                document_id=document_id,
                widened=True,
            )
        if len(widened) - len(text) >= len(needle):
            break

    return Passage(path=path, text=text, document_id=document_id)


def _passage_at(
    segmentation: Segmentation,
    addressable: AddressableText,
    anchor: Anchor,
    *,
    stored_path: list[dict[str, Any]],
    document_id: str | None,
) -> Passage:
    """Build a passage around an anchor found in the addressable text.

    The window is the passage the quotation *begins* in, extended to the end of the passage
    it finishes in — the same rule `verification` uses when it attributes a span that runs
    across a paragraph break.
    """
    position = addressable.segment_at(anchor.start)
    if position is None:
        raise PassageNotFound("this revision has no addressable text to open")

    span = addressable.span_of(position)
    assert span is not None  # segment_at only returns positions that have one
    opens, closes = span
    widened = anchor.end > closes
    if widened:
        last = addressable.segment_at(max(anchor.end - 1, anchor.start))
        end_span = addressable.span_of(last) if last is not None else None
        closes = max(closes, end_span[1] if end_span else anchor.end)

    return Passage(
        path=segmentation.locator_path(position),
        text=addressable.joined[opens:closes],
        start=anchor.start - opens,
        end=anchor.end - opens,
        document_id=document_id,
        widened=widened,
        method=anchor.method,
        similarity=anchor.similarity,
        ambiguous=anchor.ambiguous,
        stored_path=stored_path,
    )


def open_evidence(
    segmentation: Segmentation,
    path: list[dict[str, Any]],
    selector: dict[str, Any],
    *,
    document_id: str | None = None,
) -> Passage:
    """Open the source at a piece of evidence, re-anchoring it if the text has moved on.

    The recorded position is tried first and is almost always right: a snapshot is opened
    against its own revision, where nothing has changed since the quotation was verified.
    That path is a substring search over one passage, and it stays the fast one.

    When it fails — because the text has been edited, or because the locator names a
    position this revision no longer has — the quotation is looked for across the whole work
    by `reanchor`, and the passage it now sits in is returned instead. The result says which
    rung answered and where the evidence used to point, because a fuzzy match presented
    identically to a verbatim one is a citation the reader cannot weigh.
    """
    quotation = selector.get("exact", "")

    named: Passage | None = None
    try:
        named = find_passage(segmentation, path, quotation, document_id=document_id)
    except PassageNotFound:
        # The structure itself has changed under the locator. Nothing to fall back to but
        # the quotation, which is exactly the case a selector exists for.
        named = None

    # Accepted only when the quotation sits wholly inside the passage it named. A result
    # that had to widen is ambiguous between the two cases widening covers — a quotation
    # genuinely spanning a paragraph break, and one that an edit has pushed into the next
    # passage — and only re-anchoring can tell them apart. It agrees with the fast path in
    # the first case and corrects it in the second.
    if named is not None and named.located and not named.widened:
        return named

    addressable = AddressableText.of(segmentation)
    anchor = reanchor_selector(addressable.joined, selector)
    if anchor is None:
        if named is not None:
            return named
        raise PassageNotFound(
            "this quotation is not in the text any more, and the position it recorded is "
            "not in this revision either"
        )

    return _passage_at(
        segmentation,
        addressable,
        anchor,
        stored_path=path,
        document_id=document_id,
    )

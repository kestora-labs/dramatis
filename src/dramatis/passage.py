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

**A quotation that cannot be found is reported, not papered over.** The span comes back
empty and the passage is still returned, because showing the right passage without a
highlight is more use than showing nothing. Recovering a quotation whose text has since been
edited is 2.4, and this is the surface that will report when it is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dramatis.segmentation import DEFAULT_SEGMENT_TYPE, Segmentation, SegmentationSpec
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

    @property
    def located(self) -> bool:
        return self.start is not None


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

"""Dividing a text into an ordered path of typed segments.

Invariant 1 says structural position is a path of typed segments whose *types are data*.
This module supplies the mechanism and no vocabulary: a caller describes its own structure
as an ordered list of rules, outermost first, and gets back segments carrying those types.
Nothing here knows what a chapter, a panel, or a scene is, and nothing here should learn.

When no rules are given, the text is divided into a flat sequence of ``section`` segments on
blank-line boundaries. That is the honest default for a text whose structure is not yet
known: it is always available, it never invents a hierarchy, and phase 4 replaces it with an
inferred-and-confirmed structure map.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Anchor = Literal["start", "end"]

# A block boundary: the start of the text, or the first non-space after a blank line.
BLOCK_PATTERN = re.compile(r"(?:\A|\n[ \t]*\n)[ \t]*(?=\S)")

DEFAULT_SEGMENT_TYPE = "section"


class SegmentationError(ValueError):
    """A segmentation spec could not be applied."""


@dataclass(frozen=True)
class SegmentRule:
    """How to find the start of each segment at one level of the structure.

    ``pattern`` locates a boundary. ``anchor`` says whether the segment begins where the
    match begins — the usual case, for a heading that belongs to the segment it introduces —
    or where it ends, for a separator that belongs to neither side.

    ``label_group`` names a capturing group whose text becomes the segment's label.
    """

    type: str
    pattern: re.Pattern[str]
    anchor: Anchor = "start"
    label_group: str | int | None = None

    def __post_init__(self) -> None:
        if not self.type:
            raise SegmentationError("a segment rule needs a type")
        if self.anchor not in ("start", "end"):
            raise SegmentationError(f"unknown anchor {self.anchor!r}")


@dataclass(frozen=True)
class SegmentationSpec:
    """An ordered list of rules, outermost first."""

    rules: tuple[SegmentRule, ...]

    def __post_init__(self) -> None:
        if not self.rules:
            raise SegmentationError("a segmentation spec needs at least one rule")
        types = [rule.type for rule in self.rules]
        if len(set(types)) != len(types):
            raise SegmentationError(f"segment types must be distinct, got {types}")

    @property
    def segment_types(self) -> list[str]:
        """The vocabulary this spec produces, in the order a work declares it."""
        return [rule.type for rule in self.rules]

    @classmethod
    def flat(cls, segment_type: str = DEFAULT_SEGMENT_TYPE) -> SegmentationSpec:
        """A single flat level, divided on blank lines.

        Anchored at the match start rather than its end, so the blank lines fall inside the
        following segment and the segments tile the text completely. Blank lines carry no
        meaning worth preserving, and a gap would leave offsets that no locator can name.
        """
        return cls((SegmentRule(type=segment_type, pattern=BLOCK_PATTERN, anchor="start"),))


DEFAULT_SPEC = SegmentationSpec.flat()


@dataclass(frozen=True)
class Segment:
    """One structural unit, with its span in the document text."""

    type: str
    index: int
    depth: int
    start: int
    end: int
    label: str | None = None
    parent: int | None = None

    @property
    def span(self) -> tuple[int, int]:
        return self.start, self.end


@dataclass(frozen=True)
class Segmentation:
    """The result of segmenting one document."""

    text: str
    segments: tuple[Segment, ...]
    segment_types: tuple[str, ...]
    preamble: tuple[int, int] = (0, 0)

    def __len__(self) -> int:
        return len(self.segments)

    def __iter__(self):
        return iter(self.segments)

    def text_of(self, position: int) -> str:
        segment = self.segments[position]
        return self.text[segment.start : segment.end]

    @property
    def preamble_text(self) -> str:
        start, end = self.preamble
        return self.text[start:end]

    def locator_path(self, position: int) -> list[dict[str, Any]]:
        """The schema-shaped path to a segment, outermost first."""
        chain: list[Segment] = []
        current: int | None = position
        while current is not None:
            segment = self.segments[current]
            chain.append(segment)
            current = segment.parent
        chain.reverse()

        path: list[dict[str, Any]] = []
        for segment in chain:
            step: dict[str, Any] = {"type": segment.type, "index": segment.index}
            if segment.label:
                step["label"] = segment.label
            path.append(step)
        return path

    def leaves(self) -> list[int]:
        """Positions of segments with no children — the units extraction iterates over."""
        parents = {segment.parent for segment in self.segments if segment.parent is not None}
        return [position for position in range(len(self.segments)) if position not in parents]


@dataclass(frozen=True)
class _Boundary:
    """Where one segment ends and the next begins.

    The two offsets differ when a rule matches a separator rather than a heading. A heading
    belongs to the segment it introduces, so both offsets are the match start. A separator
    belongs to neither side, so the previous segment closes before it and the next opens
    after it — otherwise the separator is silently absorbed into the segment above.
    """

    opens_at: int
    closes_previous_at: int
    depth: int
    label: str | None


def _boundaries(text: str, spec: SegmentationSpec) -> list[_Boundary]:
    found: list[_Boundary] = []
    for depth, rule in enumerate(spec.rules):
        for match in rule.pattern.finditer(text):
            label: str | None = None
            if rule.label_group is not None:
                try:
                    label = match.group(rule.label_group)
                except (IndexError, re.error) as error:
                    raise SegmentationError(
                        f"rule for {rule.type!r} has no capturing group {rule.label_group!r}"
                    ) from error
            found.append(
                _Boundary(
                    opens_at=match.start() if rule.anchor == "start" else match.end(),
                    closes_previous_at=match.start(),
                    depth=depth,
                    label=label.strip() if label else None,
                )
            )

    # Sort by position, then by depth so an outer boundary opens before an inner one that
    # starts at the same offset.
    found.sort(key=lambda boundary: (boundary.opens_at, boundary.depth))
    return found


def segment_text(text: str, spec: SegmentationSpec | None = None) -> Segmentation:
    """Divide ``text`` according to ``spec``.

    Content before the first boundary is not forced into a segment. It is reported as the
    preamble, because front matter genuinely is not part of the first unit of the work, and
    silently absorbing it would attribute its content to a position it does not occupy.

    If no boundary matches at all, the whole text becomes a single segment of the outermost
    type. A spec that finds nothing is more likely to be a spec that does not fit this text
    than an instruction to produce nothing.
    """
    spec = spec or DEFAULT_SPEC
    boundaries = _boundaries(text, spec)

    if not boundaries:
        whole = Segment(
            type=spec.rules[0].type, index=1, depth=0, start=0, end=len(text), label=None
        )
        return Segmentation(text=text, segments=(whole,), segment_types=tuple(spec.segment_types))

    preamble_end = boundaries[0].closes_previous_at

    segments: list[Segment] = []
    open_stack: list[int] = []  # positions of currently open segments, outermost first
    sibling_counts: dict[tuple[int | None, int], int] = {}

    def close(position: int, end: int) -> None:
        segment = segments[position]
        segments[position] = Segment(
            type=segment.type,
            index=segment.index,
            depth=segment.depth,
            start=segment.start,
            end=end,
            label=segment.label,
            parent=segment.parent,
        )

    for boundary in boundaries:
        while open_stack and segments[open_stack[-1]].depth >= boundary.depth:
            close(open_stack.pop(), boundary.closes_previous_at)

        parent = open_stack[-1] if open_stack else None
        key = (parent, boundary.depth)
        sibling_counts[key] = sibling_counts.get(key, 0) + 1

        segments.append(
            Segment(
                type=spec.rules[boundary.depth].type,
                index=sibling_counts[key],
                depth=boundary.depth,
                start=boundary.opens_at,
                end=len(text),
                label=boundary.label,
                parent=parent,
            )
        )
        open_stack.append(len(segments) - 1)

    while open_stack:
        close(open_stack.pop(), len(text))

    return Segmentation(
        text=text,
        segments=tuple(segments),
        segment_types=tuple(spec.segment_types),
        preamble=(0, preamble_end),
    )

"""The verbatim verification gate.

Invariant 3: every quotation attached to a node or edge must be found verbatim in the
source text by a programmatic check, and extractions failing that check are *rejected*,
not surfaced with a warning. This module is where that stops being a policy and becomes
code the pipeline has to pass through.

A warning would be worse than nothing here. It would leave an unverifiable quotation in
the graph, attached to a real edge, distinguishable from a genuine one only by reading a
log nobody reads. Rejection removes it.

Two decisions shape how the rejection is scoped.

**Rejection is per interaction, not per run.** One invented quotation should not discard
sixty windows of correct work.

**Unless too many fail, in which case the run is rejected.** A pipeline that silently drops
most of what it found is not producing a sparse analysis, it is producing a misleading one:
the graph looks plausible, the missing edges leave no trace, and nothing downstream can
tell. Past a threshold the whole extraction is refused, which is loud and recoverable in a
way a thin graph is not.

**Where a quotation sits is a separate question from whether it is real.** A quotation
genuinely in the work but attributed to the wrong passage is evidence with a bad locator,
not an invention: it is relocated, not rejected. The same goes for one that runs across a
paragraph break — a line of dialogue and the reply it draws are one span of the work even
though they are two passages — which is attributed to the passage where it begins.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from dramatis.extraction import Extraction, ObservedInteraction
from dramatis.segmentation import Segmentation
from dramatis.text import normalise_whitespace

DEFAULT_MAX_REJECTION_RATE = 0.25
"""Above this share of unverifiable quotations, the whole extraction is refused.

Not a quality target — a smoke alarm. A model quoting accurately fails a few percent at
most, usually on typography. A quarter means something is wrong with the prompt, the
model, or the text it was given, and the graph that would come out the other side should
not be trusted or shown.
"""

MIN_CHECKED_FOR_RATE = 5
"""Below this many quotations, the rate is too noisy to act on and only the individual
rejections apply."""


class VerificationError(Exception):
    """Too large a share of quotations could not be verified."""


@dataclass(frozen=True)
class Rejection:
    """One interaction removed, and why."""

    interaction: ObservedInteraction
    reason: str

    def __str__(self) -> str:
        first, second = self.interaction.participants
        return f"{first} / {second}: {self.reason}"


@dataclass(frozen=True)
class Verification:
    """What survived the gate, and what did not."""

    verified: tuple[ObservedInteraction, ...] = ()
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)
    relocated: int = 0
    checked: int = 0

    @property
    def rejected(self) -> int:
        return len(self.rejections)

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.checked if self.checked else 0.0

    def __len__(self) -> int:
        return len(self.verified)


def _occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    count = start = 0
    while (index := haystack.find(needle, start)) != -1:
        count += 1
        start = index + 1
    return count


def _segment_contains(segmentation: Segmentation, position: int, needle: str) -> bool:
    segment = segmentation.segments[position]
    text = normalise_whitespace(segmentation.text[segment.start : segment.end])
    return needle in text


@dataclass(frozen=True)
class _AddressableText:
    """The work's leaf passages joined, with a map back to the passage each offset sits in.

    Searching this rather than the raw text means a quotation is checked against exactly
    the material a locator can name, and a match yields the passage directly. It also lets
    a quotation that runs across a paragraph break be attributed to the passage where it
    begins, instead of matching nowhere because no single passage holds all of it.
    """

    joined: str
    spans: tuple[tuple[int, int, int], ...]  # (start, end, segment position)

    @classmethod
    def of(cls, segmentation: Segmentation) -> _AddressableText:
        parts: list[str] = []
        spans: list[tuple[int, int, int]] = []
        cursor = 0
        for position in segmentation.leaves():
            segment = segmentation.segments[position]
            text = normalise_whitespace(segmentation.text[segment.start : segment.end])
            if not text:
                continue
            if parts:
                cursor += 1  # the single space the join inserts
            parts.append(text)
            spans.append((cursor, cursor + len(text), position))
            cursor += len(text)
        return cls(joined=" ".join(parts), spans=tuple(spans))

    def find(self, needle: str) -> int | None:
        """Return the passage a quotation begins in, or None if it is not present."""
        offset = self.joined.find(needle)
        if offset < 0:
            return None
        for start, end, position in self.spans:
            if start <= offset < end:
                return position
        return self.spans[-1][2] if self.spans else None


def verify(
    interactions: Iterable[ObservedInteraction] | Extraction,
    segmentation: Segmentation,
    *,
    max_rejection_rate: float = DEFAULT_MAX_REJECTION_RATE,
    min_checked_for_rate: int = MIN_CHECKED_FOR_RATE,
) -> Verification:
    """Check every quotation against the source and drop the ones that are not there.

    Raises VerificationError if the share of unverifiable quotations exceeds
    ``max_rejection_rate`` over at least ``min_checked_for_rate`` quotations.
    """
    if isinstance(interactions, Extraction):
        interactions = interactions.interactions

    addressable = _AddressableText.of(segmentation)
    whole = normalise_whitespace(segmentation.text)

    verified: list[ObservedInteraction] = []
    rejections: list[Rejection] = []
    relocated = 0
    checked = 0

    for interaction in interactions:
        checked += 1
        needle = normalise_whitespace(interaction.quotation)

        if not needle:
            rejections.append(Rejection(interaction, "the quotation is empty"))
            continue

        position = interaction.segment_position
        if position is not None and _segment_contains(segmentation, position, needle):
            verified.append(interaction)
            continue

        found = addressable.find(needle)
        if found is not None:
            # Real text, wrong address — or a span running across a paragraph break, which
            # is attributed to the passage it begins in. Either way the evidence is sound
            # and only the locator was wrong.
            relocated += 1
            verified.append(replace(interaction, segment_position=found))
            continue

        if needle in whole:
            # In the work, but not in any passage a locator can name — front matter, or a
            # gap a segmentation rule deliberately left outside the text.
            rejections.append(
                Rejection(
                    interaction,
                    "the quotation lies outside any addressable passage of the work",
                )
            )
            continue

        rejections.append(
            Rejection(
                interaction,
                f"the quotation is not in the source text: {interaction.quotation[:60]!r}",
            )
        )

    result = Verification(
        verified=tuple(verified),
        rejections=tuple(rejections),
        relocated=relocated,
        checked=checked,
    )

    if checked >= min_checked_for_rate and result.rejection_rate > max_rejection_rate:
        raise VerificationError(
            f"{result.rejected} of {checked} quotations could not be verified "
            f"({result.rejection_rate:.0%}, above the {max_rejection_rate:.0%} limit). "
            "The extraction is refused: a graph missing this much of what was found "
            "would look plausible and be misleading."
        )

    return result


def count_occurrences(segmentation: Segmentation, quotation: str) -> int:
    """How many times a quotation appears in the source, whitespace-normalised.

    Exposed for callers that want to know a quotation is ambiguous — appearing in several
    places — even though ambiguity is not itself grounds for rejection.
    """
    return _occurrences(normalise_whitespace(segmentation.text), normalise_whitespace(quotation))

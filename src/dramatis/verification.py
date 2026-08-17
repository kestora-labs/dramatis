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
from typing import Protocol

from dramatis.extraction import Extraction
from dramatis.segmentation import AddressableText, Segmentation
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


class Claim(Protocol):
    """What this gate needs of anything it checks.

    A protocol rather than a base class because the two reading passes produce unrelated
    frozen dataclasses — `ObservedInteraction` and `AssertedRelation` — and neither should
    have to know about the other to be verifiable.
    """

    participants: tuple[str, str]
    quotation: str
    segment_position: int | None


@dataclass(frozen=True)
class Rejection:
    """One claim removed, and why."""

    interaction: Claim
    reason: str

    def __str__(self) -> str:
        first, second = self.interaction.participants
        return f"{first} / {second}: {self.reason}"


@dataclass(frozen=True)
class Verification:
    """What survived the gate, and what did not."""

    verified: tuple[Claim, ...] = ()
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


def verify(
    interactions: Iterable[Claim] | Extraction,
    segmentation: Segmentation,
    *,
    max_rejection_rate: float = DEFAULT_MAX_REJECTION_RATE,
    min_checked_for_rate: int = MIN_CHECKED_FOR_RATE,
) -> Verification:
    """Check every quotation against the source and drop the ones that are not there.

    Raises VerificationError if the share of unverifiable quotations exceeds
    ``max_rejection_rate`` over at least ``min_checked_for_rate`` quotations.

    Any claim carrying a quotation goes through here, not only an observed interaction:
    **4.3**'s asserted relationships are checked by this same gate and against the reference
    text they were read from. Invariant 3 does not soften because a relation was declared
    rather than enacted — a bible quotation the bible does not contain is exactly as
    unusable as an invented line of dialogue.
    """
    if isinstance(interactions, Extraction):
        interactions = interactions.interactions

    addressable = AddressableText.of(segmentation)
    whole = normalise_whitespace(segmentation.text)

    verified: list[Claim] = []
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

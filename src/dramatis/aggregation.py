"""Turning observed interactions into weighted relations.

The reduce half. Extraction reported interactions between names on the page; resolution
decided which names denote which characters. Aggregation rewrites the first onto the
second, groups them into one edge per pair, and gives each edge a weight.

Two things are load-bearing.

**The weight basis is declared, not implied.** A number called "weight" means nothing on
its own: interactions counted, passages counted, and words exchanged are all defensible
and none is comparable with another. Every aggregation carries the basis it was computed
on, and comparing two that disagree is an error rather than a silently wrong diff.

**An interaction that cannot be rewritten is dropped, not guessed at.** A participant
whose surface form resolved to nothing — an alias dropped as ambiguous, most often — takes
its interaction with it. Attaching the edge to a plausible-looking character instead would
produce a graph that is confidently wrong in exactly the places a reader cannot check.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dramatis import ids
from dramatis.extraction import ObservedInteraction
from dramatis.resolution import Resolution
from dramatis.segmentation import Segmentation
from dramatis.text import normalise_whitespace

INTERACTION_PASSAGES = "interaction_passages"
"""The number of distinct passages in which a pair is shown interacting.

Distinct passages rather than reported interactions, so a window in which the model
describes one exchange three ways contributes once rather than three times. An interaction
whose quotation could not be located counts once on its own, since it is a distinct
reported instance even though its position is unknown.
"""

CONTEXT_CHARACTERS = 40
"""How much text either side of a quotation to record for re-anchoring."""


class ComparabilityError(Exception):
    """Two aggregations computed on different weight bases were compared."""


@dataclass(frozen=True)
class Evidence:
    """A passage supporting a relation, shaped for the schema."""

    quotation: str
    locator: dict[str, Any]
    prefix: str = ""
    suffix: str = ""
    note: str | None = None

    def as_schema(self) -> dict[str, Any]:
        selector: dict[str, Any] = {"exact": self.quotation}
        if self.prefix:
            selector["prefix"] = self.prefix
        if self.suffix:
            selector["suffix"] = self.suffix
        piece: dict[str, Any] = {"locator": self.locator, "selector": selector}
        if self.note:
            piece["note"] = self.note
        return piece


@dataclass(frozen=True)
class Relation:
    """One edge, weighted and evidenced."""

    id: str
    source: str
    target: str
    weight: int
    weight_basis: str
    evidence: tuple[Evidence, ...] = ()
    provenance: str = "observed"
    directed: bool = False
    types: tuple[str, ...] = ()
    """Free-text relation types, e.g. "kinship", "estrangement".

    Empty for observed relations, which count contact rather than naming it. An asserted
    relation is a *typed claim* — the bible does not say two characters interacted, it says
    they are estranged siblings — and dropping the type would leave 4.4's overlay comparing
    a declaration against an enactment with the content of the declaration thrown away.
    """

    @property
    def endpoints(self) -> frozenset[str]:
        return frozenset((self.source, self.target))

    def as_schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "directed": self.directed,
            "weight": self.weight,
            "weight_basis": self.weight_basis,
            "provenance": self.provenance,
            "evidence": [piece.as_schema() for piece in self.evidence],
            **({"types": list(self.types)} if self.types else {}),
        }


@dataclass(frozen=True)
class Aggregation:
    """Every relation, and the basis its weights were computed on."""

    relations: tuple[Relation, ...] = ()
    weight_basis: str = INTERACTION_PASSAGES
    dropped: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.relations)

    def relation_between(self, first: str, second: str) -> Relation | None:
        wanted = frozenset((first, second))
        return next((r for r in self.relations if r.endpoints == wanted), None)

    def heaviest(self) -> Relation | None:
        return max(self.relations, key=lambda r: r.weight, default=None)

    def comparable_with(self, other: Aggregation) -> bool:
        return self.weight_basis == other.weight_basis


def require_comparable(*aggregations: Aggregation) -> str:
    """Return the shared weight basis, or raise if they disagree.

    Anything that ranks, diffs, or renders two aggregations together must call this first.
    Weights on different bases are different quantities wearing the same name, and
    comparing them produces a chart that looks right and is meaningless.
    """
    bases = {aggregation.weight_basis for aggregation in aggregations}
    if len(bases) > 1:
        raise ComparabilityError(
            "these aggregations were computed on different weight bases "
            f"({', '.join(sorted(bases))}); their weights are not comparable"
        )
    return bases.pop() if bases else INTERACTION_PASSAGES


def require_comparable_snapshots(*documents: dict[str, Any]) -> None:
    """Refuse to compare stored snapshots that were not asked the same question.

    Two snapshots are a reading of one corpus over time only if the analysis was held still
    while the text moved (Invariant 4). A differing weight basis makes the numbers different
    quantities; a differing prompt makes them answers to differently-worded questions. Either
    way the comparison would look right and mean nothing.

    Anything that diffs, ranks, or overlays two snapshots calls this first.
    """
    # Checked before the prompt hash, which a change of setting necessarily changes too.
    # Both refusals would be correct; only this one names the decision somebody made
    # rather than two digests they cannot read (D19).
    collectives = {
        run.get("parameters", {}).get("collectives_are_actors")
        for document in documents
        for run in document.get("analysis_runs") or []
    }
    if len(collectives - {None}) > 1:
        raise ComparabilityError(
            "these snapshots were analysed with collectives counted as actors in one and "
            "not the other; a graph whose nodes are people and one whose nodes are people "
            "and groups are not two readings of the same corpus"
        )

    prompts: set[str] = set()
    unknown = 0
    for document in documents:
        runs = document.get("analysis_runs") or []
        digests = {run.get("prompt_sha256") for run in runs}
        if not digests or None in digests:
            unknown += 1
        prompts.update(digest for digest in digests if digest)

    if unknown:
        raise ComparabilityError(
            f"{unknown} of these snapshots record no prompt hash, so whether they were "
            "produced under the same instructions is unknowable rather than merely unknown. "
            "They predate the prompt being recorded; re-run the analysis to compare them."
        )
    if len(prompts) > 1:
        raise ComparabilityError(
            "these snapshots were produced under different extraction prompts "
            f"({', '.join(sorted(short[:12] for short in prompts))}); they are answers to "
            "differently-worded questions and are not comparable, whatever their "
            "prompt_version claims"
        )

    bases = {
        relation.get("weight_basis")
        for document in documents
        for relation in document.get("relations") or []
        if relation.get("weight_basis")
    }
    if len(bases) > 1:
        raise ComparabilityError(
            "these snapshots were computed on different weight bases "
            f"({', '.join(sorted(bases))}); their weights are not comparable"
        )


def _context(segmentation: Segmentation, position: int, quotation: str) -> tuple[str, str]:
    """Return the text either side of a quotation within its segment."""
    segment = segmentation.segments[position]
    haystack = normalise_whitespace(segmentation.text[segment.start : segment.end])
    needle = normalise_whitespace(quotation)
    offset = haystack.find(needle)
    if offset < 0:
        return "", ""
    before = haystack[max(0, offset - CONTEXT_CHARACTERS) : offset]
    after = haystack[offset + len(needle) : offset + len(needle) + CONTEXT_CHARACTERS]
    return before.strip(), after.strip()


def _document_at(
    segmentation: Segmentation,
    position: int | None,
    spans: Sequence[tuple[int, int, str]] | None,
) -> str | None:
    """Which document a passage falls in.

    A revision of a folder is many documents concatenated, so the document a quotation
    belongs to is decided by where its passage starts. Naming the revision's first document
    for everything — which is what a single-document corpus let us get away with — would
    attribute every quotation in a novel to chapter one.
    """
    if not spans:
        return None
    if position is None:
        return spans[0][2]

    offset = segmentation.segments[position].start
    for start, end, document_id in spans:
        if start <= offset < end:
            return document_id
    # Past the end of the last document: an offset that no document covers is not a passage
    # anyone can cite, so it is left unattributed rather than given to the nearest.
    return None


def _locator(
    segmentation: Segmentation, position: int | None, document_id: str | None
) -> dict[str, Any]:
    locator: dict[str, Any] = {}
    if document_id:
        locator["document_id"] = document_id
    locator["path"] = (
        segmentation.locator_path(position)
        if position is not None
        else [
            {
                "type": segmentation.segment_types[0] if segmentation.segment_types else "section",
                "index": 1,
            }
        ]
    )
    return locator


def aggregate(
    interactions: Sequence[ObservedInteraction] | Iterable[ObservedInteraction],
    resolution: Resolution,
    segmentation: Segmentation,
    *,
    document_id: str | None = None,
    document_spans: Sequence[tuple[int, int, str]] | None = None,
    weight_basis: str = INTERACTION_PASSAGES,
) -> Aggregation:
    """Group interactions into one relation per pair of resolved characters.

    Takes a sequence of interactions rather than an extraction, so that phase 1.7 can
    filter unverifiable ones out *before* aggregation. Filtering afterwards would leave
    weights counting evidence that had since been rejected.
    """
    return aggregate_claims(
        interactions,
        resolution,
        segmentation,
        document_id=document_id,
        document_spans=document_spans,
        weight_basis=weight_basis,
        provenance="observed",
        noun="an interaction",
    )


def aggregate_claims(
    claims: Iterable[Any],
    resolution: Resolution,
    segmentation: Segmentation,
    *,
    document_id: str | None = None,
    document_spans: Sequence[tuple[int, int, str]] | None = None,
    weight_basis: str = INTERACTION_PASSAGES,
    provenance: str = "observed",
    noun: str = "an interaction",
) -> Aggregation:
    """The grouping both reading passes share: claims about pairs into one edge per pair.

    A claim is anything carrying ``participants``, ``quotation``, ``note`` and
    ``segment_position``; **4.3**'s asserted relationships add ``types``, which are unioned
    onto the edge. One implementation rather than two because the parts that must not drift
    are the fiddly ones — how a passage is keyed when its quotation could not be located, how
    context is captured, which document an offset falls in — and a second copy of those is a
    second place for evidence to be attributed to the wrong document.

    What genuinely differs between the passes is passed in: ``provenance`` (which becomes
    part of the relation's identity, so a declared pair and an enacted pair are two edges),
    ``weight_basis`` (statements are not passages of contact), and ``noun`` for the warnings
    a person reads.
    """
    grouped: dict[frozenset[str], dict[str, Any]] = {}
    warnings: list[str] = []
    dropped = 0

    for interaction in claims:
        first, second = interaction.participants
        source = resolution.character_for(first)
        target = resolution.character_for(second)

        if source is None or target is None:
            unresolved = first if source is None else second
            dropped += 1
            warnings.append(f"dropped {noun}: the name {unresolved!r} resolved to no character")
            continue

        if source == target:
            # Two surface forms that resolution decided are one character. The model saw a
            # relation; there is nobody on the other end of it.
            dropped += 1
            warnings.append(
                f"dropped {noun} between {first!r} and {second!r}: both resolved "
                f"to {source}, so there is no pair"
            )
            continue

        key = frozenset((source, target))
        bucket = grouped.setdefault(
            key, {"passages": {}, "endpoints": (source, target), "types": []}
        )
        for kind in getattr(interaction, "types", ()):
            if kind not in bucket["types"]:
                bucket["types"].append(kind)

        # Keyed by passage where known, else by the quotation itself: an unlocated
        # interaction is still a distinct reported instance.
        passage_key: Any = (
            interaction.segment_position
            if interaction.segment_position is not None
            else ("unlocated", normalise_whitespace(interaction.quotation))
        )
        if passage_key in bucket["passages"]:
            continue

        prefix, suffix = (
            _context(segmentation, interaction.segment_position, interaction.quotation)
            if interaction.segment_position is not None
            else ("", "")
        )
        bucket["passages"][passage_key] = Evidence(
            quotation=interaction.quotation,
            locator=_locator(
                segmentation,
                interaction.segment_position,
                _document_at(segmentation, interaction.segment_position, document_spans)
                or document_id,
            ),
            prefix=prefix,
            suffix=suffix,
            note=interaction.note,
        )

    relations: list[Relation] = []
    for bucket in grouped.values():
        source, target = bucket["endpoints"]
        left, right = sorted((source, target))
        evidence = tuple(
            bucket["passages"][passage]
            for passage in sorted(bucket["passages"], key=lambda p: (isinstance(p, tuple), p))
        )
        relations.append(
            Relation(
                id=ids.relation_id(left, right, provenance),
                source=left,
                target=right,
                weight=len(evidence),
                weight_basis=weight_basis,
                evidence=evidence,
                provenance=provenance,
                types=tuple(sorted(bucket["types"])),
            )
        )

    relations.sort(key=lambda relation: (-relation.weight, relation.id))
    return Aggregation(
        relations=tuple(relations),
        weight_basis=weight_basis,
        dropped=dropped,
        warnings=tuple(warnings),
    )

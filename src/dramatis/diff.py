"""What changed between two snapshots, and whether anything can be blamed for it.

This is the module the project exists for. A snapshot on its own is a picture of a cast; two
snapshots are a story about how the cast moved, and the whole value of that story rests on
one question the fixture asks before any of the others:

> The attribution matters as much as the change. Both drafts must be analysed by the same
> run configuration, or the diff cannot distinguish a rewrite from a better prompt.

So a diff reports **attribution** first. Two snapshots differing only in text revision say
the work changed. Differing only in analysis, the reading changed. Differing in both, nothing
can be credited to either, and the diff says so rather than picking whichever moved more.

Three further rules, each because the obvious alternative reports noise.

**Weights are compared only within a shared basis.** A weight is a number on a named scale,
and two snapshots weighed differently have no common scale for "stronger" to mean anything
on. Where the bases disagree the weight comparisons are withheld and the reason given, the
same refusal 2.1 makes when printing a weight and 2.5 makes when offering to filter on one.

**A merged character does not empty its edges into the void.** When two characters become
one, every relation that touched the absorbed character would otherwise read as removed and
a matching one as added — dozens of spurious changes describing a single act of curation.
Relations are compared through the merge, so what is reported is what actually moved.

**Identity is claimed from the record, not guessed.** A merge is recognised because the
surviving character now lists the absorbed one's name among its own surface forms, which is
exactly what the registry writes down when it merges two. Where there is no such record the
change is reported as a plain addition or removal, because "these two are the same person"
is a claim, and an unevidenced one is worse than an unexplained pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dramatis.snapshot import canonical_json

ADDED = "added"
REMOVED = "removed"
MERGED = "merged"
SPLIT = "split"

STRENGTHENED = "strengthened"
WEAKENED = "weakened"
RETYPED = "retyped"

TEXT = "text"
ANALYSIS = "analysis"
BOTH = "both"
SAME = "same"


class DiffError(Exception):
    """Two snapshots that cannot meaningfully be compared."""


@dataclass(frozen=True)
class CharacterChange:
    id: str
    name: str
    kind: str
    """``added``, ``removed``, ``merged`` or ``split``."""
    counterparts: tuple[str, ...] = ()
    """For a merge, what it became. For a split, what came out of it."""


@dataclass(frozen=True)
class RelationChange:
    id: str
    source: str
    target: str
    kinds: tuple[str, ...]
    """What changed about it. A relation may strengthen *and* be retyped at once, and two
    entries for one edge would double-count a single change."""
    weight_before: float | None = None
    weight_after: float | None = None
    types_before: tuple[str, ...] = ()
    types_after: tuple[str, ...] = ()

    @property
    def delta(self) -> float | None:
        if self.weight_before is None or self.weight_after is None:
            return None
        return self.weight_after - self.weight_before


@dataclass(frozen=True)
class Diff:
    before: str
    after: str
    attribution: str
    """``text``, ``analysis``, ``both`` or ``same`` — which axis the change can be laid at."""
    weights_comparable: bool
    weight_basis: str | None
    characters: tuple[CharacterChange, ...] = ()
    relations: tuple[RelationChange, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def characters_of(self, kind: str) -> tuple[CharacterChange, ...]:
        return tuple(change for change in self.characters if change.kind == kind)

    def relations_of(self, kind: str) -> tuple[RelationChange, ...]:
        return tuple(change for change in self.relations if kind in change.kinds)

    @property
    def empty(self) -> bool:
        return not self.characters and not self.relations


def _surface_forms(character: dict[str, Any]) -> set[str]:
    """Every name that denotes this character, folded for comparison."""
    forms = [character.get("name", ""), *(character.get("aliases") or [])]
    return {form.strip().casefold() for form in forms if form and form.strip()}


def _configuration(document: dict[str, Any]) -> Any:
    """What the analysis behind a snapshot *was*, as opposed to when it ran.

    A run identifier includes its start time (D33), so comparing identifiers would call two
    executions of one configuration two different analyses — and then report every diff as
    differing on both axes, which is the answer that credits a change to nothing. The
    configuration is the model, the prompt actually sent, the pipeline and the parameters.

    Falls back to the run identifier when the document does not carry the run, which the
    schema permits: an identifier that is too strict is better than an attribution that is
    too generous.
    """
    wanted = document["snapshot"]["analysis_run_id"]
    for run in document.get("analysis_runs") or []:
        if run.get("id") == wanted:
            return (
                run.get("model"),
                run.get("provider"),
                run.get("prompt_version"),
                run.get("prompt_sha256"),
                run.get("pipeline_version"),
                canonical_json(run.get("parameters") or {}),
            )
    return wanted


def attribution_of(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Which axis separates two snapshots.

    The analysis axis is compared by configuration rather than by run identifier, for the
    reason `_configuration` gives.
    """
    text = before["snapshot"]["text_revision_id"] != after["snapshot"]["text_revision_id"]
    analysis = _configuration(before) != _configuration(after)

    if text and analysis:
        return BOTH
    if text:
        return TEXT
    if analysis:
        return ANALYSIS
    return SAME


def _character_changes(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[CharacterChange], dict[str, str]]:
    """Characters added, removed, merged or split, and where the merges went.

    The returned map sends an absorbed character's identifier to its survivor, so relations
    can be compared through a merge instead of reporting every edge it touched twice.
    """
    was = {character["id"]: character for character in before}
    now = {character["id"]: character for character in after}

    changes: list[CharacterChange] = []
    merged_into: dict[str, str] = {}

    for identifier, character in was.items():
        if identifier in now:
            continue
        # Gone. Either absorbed by somebody who now answers to its name, or simply absent.
        name = character.get("name", "").strip().casefold()
        survivors = tuple(other["id"] for other in after if name and name in _surface_forms(other))
        if survivors:
            changes.append(
                CharacterChange(identifier, character.get("name", ""), MERGED, survivors)
            )
            merged_into[identifier] = survivors[0]
        else:
            changes.append(CharacterChange(identifier, character.get("name", ""), REMOVED))

    for identifier, character in now.items():
        if identifier in was:
            continue
        name = character.get("name", "").strip().casefold()
        # A split is only a split if the character it came out of is still there; otherwise
        # the pair is a rename, and calling it a split would invent a second person.
        parents = tuple(
            other["id"]
            for other in before
            if other["id"] in now and name and name in _surface_forms(other)
        )
        if parents:
            changes.append(CharacterChange(identifier, character.get("name", ""), SPLIT, parents))
        else:
            changes.append(CharacterChange(identifier, character.get("name", ""), ADDED))

    return changes, merged_into


def _endpoint(identifier: str, merged_into: dict[str, str]) -> str:
    return merged_into.get(identifier, identifier)


def _key(relation: dict[str, Any], merged_into: dict[str, str]) -> tuple[str, str]:
    """A relation's identity as a pair of characters, seen through any merge.

    Not the stored identifier: that is derived from the endpoints as they were named at the
    time, so a merge would make the same edge look like a different one.
    """
    return tuple(  # type: ignore[return-value]
        sorted(
            (_endpoint(relation["source"], merged_into), _endpoint(relation["target"], merged_into))
        )
    )


def _relation_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    merged_into: dict[str, str],
    *,
    weights_comparable: bool,
) -> list[RelationChange]:
    was = {_key(relation, merged_into): relation for relation in before}
    now = {_key(relation, merged_into): relation for relation in after}

    changes: list[RelationChange] = []

    for key, relation in was.items():
        if key not in now:
            changes.append(
                RelationChange(
                    relation["id"],
                    key[0],
                    key[1],
                    (REMOVED,),
                    weight_before=relation.get("weight"),
                    types_before=tuple(relation.get("types") or ()),
                )
            )

    for key, relation in now.items():
        if key not in was:
            changes.append(
                RelationChange(
                    relation["id"],
                    key[0],
                    key[1],
                    (ADDED,),
                    weight_after=relation.get("weight"),
                    types_after=tuple(relation.get("types") or ()),
                )
            )
            continue

        earlier = was[key]
        kinds: list[str] = []

        if weights_comparable:
            first, second = earlier.get("weight"), relation.get("weight")
            if first is not None and second is not None and first != second:
                kinds.append(STRENGTHENED if second > first else WEAKENED)

        types_before = tuple(earlier.get("types") or ())
        types_after = tuple(relation.get("types") or ())
        if set(types_before) != set(types_after):
            kinds.append(RETYPED)

        if kinds:
            changes.append(
                RelationChange(
                    relation["id"],
                    key[0],
                    key[1],
                    tuple(kinds),
                    weight_before=earlier.get("weight"),
                    weight_after=relation.get("weight"),
                    types_before=types_before,
                    types_after=types_after,
                )
            )

    return changes


def _basis_of(relations: list[dict[str, Any]]) -> str | None:
    bases = {relation.get("weight_basis") for relation in relations if relation.get("weight_basis")}
    return bases.pop() if len(bases) == 1 else None


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> Diff:
    """Compare two snapshot documents.

    Raises DiffError if they describe different works, which is not a comparison anybody
    means to make: two novels have no characters in common by construction, so every node
    and edge would be reported as added or removed and the result would be a list of
    everything rather than a diff.
    """
    first_work = (before.get("works") or [{}])[0].get("id")
    second_work = (after.get("works") or [{}])[0].get("id")
    if first_work != second_work:
        raise DiffError(
            f"these snapshots are of different works ({first_work!r} and {second_work!r}); "
            "everything in them would be reported as added or removed"
        )

    warnings: list[str] = []

    before_relations = before.get("relations") or []
    after_relations = after.get("relations") or []
    before_basis = _basis_of(before_relations)
    after_basis = _basis_of(after_relations)
    weights_comparable = before_basis is not None and before_basis == after_basis

    if not weights_comparable:
        warnings.append(
            "weights are not comparable between these snapshots "
            f"({before_basis or 'mixed'} against {after_basis or 'mixed'}), so nothing is "
            "reported as strengthened or weakened"
        )

    attribution = attribution_of(before, after)
    if attribution == BOTH:
        warnings.append(
            "the text revision and the analysis run both differ, so no change below can be "
            "credited to either"
        )

    characters, merged_into = _character_changes(
        before.get("characters") or [], after.get("characters") or []
    )
    relations = _relation_changes(
        before_relations, after_relations, merged_into, weights_comparable=weights_comparable
    )

    return Diff(
        before=before["snapshot"]["id"],
        after=after["snapshot"]["id"],
        attribution=attribution,
        weights_comparable=weights_comparable,
        weight_basis=before_basis if weights_comparable else None,
        characters=tuple(characters),
        relations=tuple(relations),
        warnings=tuple(warnings),
    )

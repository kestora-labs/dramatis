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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dramatis import ids
from dramatis.snapshot import canonical_json

ADDED = "added"
REMOVED = "removed"
MERGED = "merged"
SPLIT = "split"
RENAMED = "renamed"
"""Present in both editions under different names, by a declared correspondence (**6.4**).

The confidante called Hesper in 1889 and Perdita in 1903 is not a character who left and
another who arrived. Without this she is reported as one removal and one addition, which is
two false statements where the truth is that nothing about her changed but her name.
"""

STRENGTHENED = "strengthened"
WEAKENED = "weakened"
RETYPED = "retyped"

TEXT = "text"
ANALYSIS = "analysis"
BOTH = "both"
SAME = "same"

EDITION = "edition"
"""The attribution when two snapshots read two editions of one work (**6.4**).

Not `text`, and the difference is the whole of fixture **D**'s complaint. `text` means the
work was rewritten and the later state supersedes the earlier. Two editions are both
authoritative, both citable and both current, and reporting the 1903 reading as a change to
the 1889 one would tell a scholar the opposite of the truth about a published text.
"""


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
    """``text``, ``analysis``, ``both``, ``same``, or ``edition`` — which axis the change can
    be laid at."""
    weights_comparable: bool
    weight_basis: str | None
    editions: tuple[str, str] | None = None
    """The two editions, when the snapshots read different editions of one work (**6.4**).

    Carried even where the attribution is ``both``, because *which* editions were compared is
    part of the citation and a reader cannot recover it from the attribution alone.
    """
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


def editions_of(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, str] | None:
    """The two editions being compared, or None if these are readings of one edition.

    Read from the work identifiers rather than from the ``edition`` field, because the
    identifier is what `ids.work_id` derived the edition into and is therefore present even
    on a document whose work entry omitted the free-text label. The label is preferred for
    display where it is there.
    """
    first = (before.get("works") or [{}])[0]
    second = (after.get("works") or [{}])[0]

    left_base, left_edition = ids.work_edition(str(first.get("id", "")))
    right_base, right_edition = ids.work_edition(str(second.get("id", "")))

    if left_base != right_base or left_edition == right_edition:
        return None
    if left_edition is None or right_edition is None:
        # One side is the unedition'd form of the same work. That is a project where an
        # edition was named for one ingest and not the other, which is a real state and not
        # a comparison of two editions.
        return None

    return (
        str(first.get("edition") or left_edition),
        str(second.get("edition") or right_edition),
    )


def attribution_of(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Which axis separates two snapshots.

    The analysis axis is compared by configuration rather than by run identifier, for the
    reason `_configuration` gives.

    **An edition difference is reported as one and never as a rewrite.** Two editions have
    different text revisions by construction, so the plain revision comparison would call
    every edition diff a change to the text — the one thing fixture **D** says must not
    happen. Where the analysis also differs the answer is still ``both``, because nothing can
    be credited to either; `Diff.editions` carries which editions those were.
    """
    edition = editions_of(before, after) is not None
    text = before["snapshot"]["text_revision_id"] != after["snapshot"]["text_revision_id"]
    analysis = _configuration(before) != _configuration(after)

    if analysis and (text or edition):
        return BOTH
    if edition:
        return EDITION
    if text:
        return TEXT
    if analysis:
        return ANALYSIS
    return SAME


def _character_changes(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    corresponding: Mapping[str, str] | None = None,
) -> tuple[list[CharacterChange], dict[str, str]]:
    """Characters added, removed, merged, split or renamed, and where each went.

    The returned map sends an absorbed character's identifier to its survivor, so relations
    can be compared through a merge instead of reporting every edge it touched twice. A
    cross-edition correspondence (**6.4**) rides the same map for the same reason: without
    it, every edge the renamed character touched is reported removed and an identical one
    added.

    ``corresponding`` maps identifiers to their counterpart in the other edition, both ways
    round, as `identity.correspondents` returns it.
    """
    corresponding = corresponding or {}
    was = {character["id"]: character for character in before}
    now = {character["id"]: character for character in after}

    changes: list[CharacterChange] = []
    merged_into: dict[str, str] = {}

    renamed: set[str] = set()
    for identifier, character in was.items():
        if identifier in now:
            continue
        counterpart = corresponding.get(identifier)
        if counterpart is not None and counterpart in now and counterpart not in was:
            # One figure under two names, and a person said so. Reported once, from the side
            # that was there first, and never as a removal plus an addition.
            changes.append(
                CharacterChange(identifier, character.get("name", ""), RENAMED, (counterpart,))
            )
            merged_into[identifier] = counterpart
            renamed.add(identifier)
            renamed.add(counterpart)

    for identifier, character in was.items():
        if identifier in now or identifier in renamed:
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
        if identifier in was or identifier in renamed:
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


def _uncorresponded(
    before: dict[str, Any], after: dict[str, Any], corresponding: Mapping[str, str] | None
) -> int:
    """How many characters sit on one side only with nothing declared about them.

    A hint rather than a fault. Some of them genuinely are in one edition and not the other,
    which is a real finding about the revision; the rest are renamings nobody has recorded
    yet, and the two are indistinguishable from here. Counting them says *look at these*
    without claiming to know which kind they are.
    """
    corresponding = corresponding or {}
    was = {character["id"] for character in before.get("characters") or []}
    now = {character["id"] for character in after.get("characters") or []}
    lonely = (was - now) | (now - was)
    return sum(1 for identifier in lonely if corresponding.get(identifier) not in (was | now))


def diff_snapshots(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    corresponding: Mapping[str, str] | None = None,
) -> Diff:
    """Compare two snapshot documents.

    Raises DiffError if they describe different works, which is not a comparison anybody
    means to make: two novels have no characters in common by construction, so every node
    and edge would be reported as added or removed and the result would be a list of
    everything rather than a diff.

    **Two editions of one work are an exception, and the reason this bullet exists.** They
    are different works by identifier (**6.4**) and they are the comparison fixture **D** is
    for. The argument above does not apply to them: they share a collection, and therefore a
    registry, so nearly every character is literally the same character. Only the ones a
    person has declared corresponded differ in identifier, and ``corresponding`` is how those
    are matched — `identity.correspondents` builds it.
    """
    first_work = (before.get("works") or [{}])[0].get("id")
    second_work = (after.get("works") or [{}])[0].get("id")
    editions = editions_of(before, after)
    if first_work != second_work and editions is None:
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
    if editions is not None:
        # Said out loud, because the numbers below look exactly like a rewrite and are not
        # one. Neither edition supersedes the other; this is a comparison of two texts that
        # are both current.
        warnings.append(
            f"these are two editions of one work ({editions[0]} against {editions[1]}), "
            "so nothing below is a change the work underwent"
        )
        uncorresponded = _uncorresponded(before, after, corresponding)
        if uncorresponded:
            warnings.append(
                f"{uncorresponded} character(s) appear in one edition and not the other with "
                "no declared correspondence; `dramatis correspond` records a renaming"
            )

    characters, merged_into = _character_changes(
        before.get("characters") or [],
        after.get("characters") or [],
        corresponding,
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
        editions=editions,
        characters=tuple(characters),
        relations=tuple(relations),
        warnings=tuple(warnings),
    )

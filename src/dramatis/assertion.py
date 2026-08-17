"""Reading what reference material *declares*, as opposed to what narrative *enacts*.

A character bible states relationships. A chapter shows them. Both produce edges between the
same characters, and fixture **C** exists because the interesting thing about a corpus is
where the two disagree — a relationship given a whole section of the bible that never once
appears on the page, and a pair who carry more scenes than anyone while the bible does not
mention them at all. Its README puts the requirement plainly: *"a pipeline that merges the
two provenance classes into one graph loses both findings."*

So this is a second reading pass, not a variation on the first, and three things keep the two
apart all the way to the snapshot.

**Provenance is part of a relation's identity.** `ids.relation_id` suffixes anything that is
not `observed`, so a declared pair and an enacted pair are two edges rather than one edge
counted twice. Merging them is the failure the fixture is built to catch.

**The weight means something different, and says so.** An observed weight counts passages in
which a pair is shown in contact; more contact is more of the same quantity. An assertion is
not a quantity. A bible that says two characters are siblings twice has not made them twice
as related — it has repeated itself. The basis is therefore `ASSERTED_STATEMENTS`, and
`require_comparable` refuses to rank or diff it against an observed weight, which is correct:
they are different quantities that would otherwise wear the same name on the same chart.

**The type is the content of the claim.** The bible does not say two characters interacted,
it says they are estranged siblings. An asserted relation carries that as `types`, because
without it 4.4's overlay would compare a declaration against an enactment having thrown away
what was declared.

What is *not* different: characters resolve once, across both passes. "Ada" in the bible and
"Ada" on the page must become the same character, or every relation would look both
undeclared and unenacted. That is what `resolution.resolve_mentions` is for.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dramatis.aggregation import Aggregation, aggregate_claims
from dramatis.extraction import (
    DEFAULT_WINDOW_CHARACTERS,
    MentionedCharacter,
    Window,
    build_windows,
    locate_quotation,
    read_prompt,
    window_text,
)
from dramatis.providers import ModelRequest, ModelResponse, Provider, ProviderError
from dramatis.resolution import Resolution
from dramatis.segmentation import Segmentation

PROMPT_VERSION = "assert-v1"
PROMPT_FILE = "assert.md"

ASSERTED_STATEMENTS = "asserted_statements"
"""What an asserted weight counts: distinct statements declaring the relation.

Named rather than shared with the observed basis so that `require_comparable` refuses to put
the two on one scale. A pair the bible states once and a pair the narrative enacts once are
not equally weighted things — the first is a settled claim about the work, the second is a
single scene — and a chart that ranked them together would look right and mean nothing.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["characters", "relationships"],
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "aliases", "kind"],
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "kind": {
                        "type": "string",
                        "enum": ["person", "collective", "entity", "unknown"],
                    },
                },
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["participants", "quotation", "types", "note"],
                "properties": {
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "quotation": {"type": "string"},
                    "types": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        },
    },
}


class AssertionFailure(Exception):
    """Reference material could not be read. The message names the window.

    Not named ``AssertionError``, which is Python's own and means something else entirely; a
    bare ``except AssertionError`` anywhere would swallow this and report a corpus as having
    declared nothing.
    """


@dataclass(frozen=True)
class AssertedRelation:
    """One relationship the reference material states, and the sentence stating it."""

    participants: tuple[str, str]
    quotation: str
    types: tuple[str, ...] = ()
    note: str | None = None
    segment_position: int | None = None
    """Which segment the quotation was found in, or None when it was not found. Recorded
    rather than rejected here; verification decides (Invariant 3)."""


@dataclass(frozen=True)
class AssertionFinding:
    window: Window
    characters: tuple[MentionedCharacter, ...] = ()
    relationships: tuple[AssertedRelation, ...] = ()


@dataclass(frozen=True)
class Assertions:
    """Everything the reference pass declared, plus what the run needs to be citable."""

    findings: tuple[AssertionFinding, ...] = ()
    prompt_version: str = PROMPT_VERSION
    model: str = ""
    provider: str = ""
    prompt_sha256: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def characters(self) -> list[MentionedCharacter]:
        return [character for finding in self.findings for character in finding.characters]

    @property
    def relationships(self) -> list[AssertedRelation]:
        return [claim for finding in self.findings for claim in finding.relationships]

    def __len__(self) -> int:
        return len(self.relationships)


def system_prompt() -> str:
    return read_prompt(PROMPT_FILE)


def prompt_sha256() -> str:
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()


def _parse(
    payload: Any, window: Window
) -> tuple[tuple[MentionedCharacter, ...], tuple[AssertedRelation, ...], list[str]]:
    if not isinstance(payload, dict):
        raise AssertionFailure(
            f"window {window.index}: expected a JSON object, got {type(payload)}"
        )

    warnings: list[str] = []

    characters: list[MentionedCharacter] = []
    for entry in payload.get("characters") or []:
        if not isinstance(entry, dict):
            warnings.append(f"window {window.index}: discarded a non-object character entry")
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            warnings.append(f"window {window.index}: discarded a character with no name")
            continue
        aliases = tuple(
            alias.strip()
            for alias in entry.get("aliases") or []
            if isinstance(alias, str) and alias.strip() and alias.strip() != name
        )
        characters.append(
            MentionedCharacter(name=name, aliases=aliases, kind=str(entry.get("kind") or "unknown"))
        )

    relationships: list[AssertedRelation] = []
    for entry in payload.get("relationships") or []:
        if not isinstance(entry, dict):
            warnings.append(f"window {window.index}: discarded a non-object relationship entry")
            continue
        participants = [
            str(name).strip()
            for name in entry.get("participants") or []
            if isinstance(name, str) and str(name).strip()
        ]
        # The schema cannot express "exactly two", so it is enforced here.
        if len(participants) != 2:
            warnings.append(
                f"window {window.index}: discarded a relationship with {len(participants)} "
                "participant(s); a relationship joins exactly two"
            )
            continue
        if participants[0] == participants[1]:
            warnings.append(
                f"window {window.index}: discarded a self-relationship for {participants[0]!r}"
            )
            continue
        quotation = str(entry.get("quotation") or "")
        if not quotation.strip():
            # An assertion with no quotation is an assertion by the model rather than by the
            # author, which is the one thing this pass must not produce (Invariant 3).
            warnings.append(
                f"window {window.index}: discarded a stated relationship between "
                f"{participants[0]!r} and {participants[1]!r} with no quotation"
            )
            continue
        types = tuple(
            str(kind).strip().lower()
            for kind in entry.get("types") or []
            if isinstance(kind, str) and str(kind).strip()
        )
        relationships.append(
            AssertedRelation(
                participants=(participants[0], participants[1]),
                quotation=quotation,
                types=types,
                note=str(entry.get("note") or "").strip() or None,
            )
        )

    return tuple(characters), tuple(relationships), warnings


def extract_assertions(
    segmentation: Segmentation,
    provider: Provider,
    *,
    target_characters: int = DEFAULT_WINDOW_CHARACTERS,
    max_tokens: int = 8192,
    effort: str | None = "medium",
) -> Assertions:
    """Read reference material and return the relationships it states.

    Windowed and read exactly as narrative is, because a character bible can be longer than a
    context window and there is nothing about reference material that makes it shorter.
    Nothing is resolved, weighted, or rejected here.
    """
    windows = build_windows(segmentation, target_characters=target_characters)
    prompt = system_prompt()

    findings: list[AssertionFinding] = []
    warnings: list[str] = []
    input_tokens = output_tokens = 0
    model = provider_name = ""

    for window in windows:
        text = window_text(segmentation, window)
        if not text.strip():
            continue

        request = ModelRequest(
            prompt=text,
            system=prompt,
            max_tokens=max_tokens,
            effort=effort,
            output_schema=RESPONSE_SCHEMA,
            metadata={"step": "assert", "window": str(window.index)},
        )

        try:
            response: ModelResponse = provider.complete(request)
        except ProviderError as error:
            raise AssertionFailure(f"window {window.index}: {error}") from error

        if response.refused:
            raise AssertionFailure(
                f"window {window.index}: the provider declined to answer. A refusal is not "
                "an empty result - the window was not read."
            )

        try:
            payload = response.json()
        except ProviderError as error:
            raise AssertionFailure(f"window {window.index}: {error}") from error

        characters, relationships, window_warnings = _parse(payload, window)
        warnings.extend(window_warnings)

        located = tuple(
            AssertedRelation(
                participants=claim.participants,
                quotation=claim.quotation,
                types=claim.types,
                note=claim.note,
                segment_position=locate_quotation(segmentation, window, claim.quotation),
            )
            for claim in relationships
        )

        findings.append(
            AssertionFinding(window=window, characters=characters, relationships=located)
        )

        model = response.model or model
        provider_name = response.provider or provider_name
        input_tokens += response.input_tokens or 0
        output_tokens += response.output_tokens or 0

    return Assertions(
        findings=tuple(findings),
        prompt_version=PROMPT_VERSION,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        model=model,
        provider=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        warnings=tuple(warnings),
    )


def aggregate_assertions(
    relationships: Sequence[AssertedRelation] | Iterable[AssertedRelation],
    resolution: Resolution,
    segmentation: Segmentation,
    *,
    document_id: str | None = None,
    document_spans: Sequence[tuple[int, int, str]] | None = None,
) -> Aggregation:
    """Group stated relationships into one asserted relation per pair.

    The grouping is `aggregation.aggregate_claims`, shared with the observed pass so the
    fiddly parts cannot drift. What this fixes are the three things that must differ: the
    provenance, which becomes part of each relation's identity; the weight basis, because
    statements are not passages of contact; and the noun in any warning a person reads.
    """
    return aggregate_claims(
        relationships,
        resolution,
        segmentation,
        document_id=document_id,
        document_spans=document_spans,
        weight_basis=ASSERTED_STATEMENTS,
        provenance="asserted",
        noun="a stated relationship",
    )

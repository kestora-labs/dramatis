"""Resolving surface forms onto a collection's character registry.

Extraction reports names as each passage wrote them: "Elizabeth" in one window, "Miss
Eliza Bennet" in another, "Lizzy" in a third. Resolution decides which of those denote one
character, and assigns the stable identifier that makes two snapshots comparable.

Three rules do most of the work, and each exists because of a specific way this goes wrong.

**The registry is what makes identity stable, not the naming function.** A form already
claimed by a registered character resolves to it, even if this run would have preferred a
different canonical name. Identifiers derived afresh each run would change whenever the
model's phrasing did, and every diff would be noise.

**Ambiguity is detected by conflict, not by vocabulary.** A surface form proposed for two
different characters anywhere in the work is dropped. That catches pronouns and common
epithets — "she", "the girl", "my dear" — without a stop-list, and so without encoding one
language's conventions into the core.

**The registry only ever grows.** Resolution can create a character or attach a new form to
an existing one. It cannot merge two that already exist, rename one, or remove one — not
because a check forbids it, but because a form the registry already claims is resolved
directly and never reaches the grouping stage at all. Merging is destructive and cannot be
reviewed after the fact, so it stays a human act (phase 5.3).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from dramatis import ids
from dramatis.extraction import Extraction, MentionedCharacter
from dramatis.providers import ModelRequest, Provider, ProviderError
from dramatis.store import RegisteredCharacter, Store, form_key

PROMPT_VERSION = "resolve-v1"

RESOLUTION_BASE_TOKENS = 8192
"""What the reply costs before any name is in it: the JSON envelope, and room to think.

Thinking shares the output budget on current models, so a base sized only for the envelope
would spend the whole allowance reasoning and truncate the answer."""

RESOLUTION_TOKENS_PER_FORM = 48
"""Worst case per surface form — every form its own group, which is what the deterministic
baseline does and what a model that declines to merge anything would produce.

Forms that *do* merge cost far less, since they add a string to an existing group rather
than a whole one. Sizing for the worst case means the budget is never the reason a cautious
grouping fails."""

RESOLUTION_MAX_TOKENS = 32_768
"""A ceiling, deliberately below the smallest output cap among current models.

Reaching it means the cast outgrew what one call can group, and the answer then is to batch
the names rather than to raise this number — see D22. Until that exists, the ceiling is what
turns "too big" into a reported failure instead of an unbounded request."""

SYSTEM_PROMPT = """\
You are given a list of names, as they appear in a narrative work, and the number of \
passages each was seen in. Some of these are different ways of writing the same \
character: a first name and a full name, a title and a surname, an epithet, an assumed \
name a character uses as a disguise.

Group the names that denote the same character. Give each group a canonical name — the \
fullest and least ambiguous form that appears in the list.

Two rules matter more than the rest.

A shorter name is not automatically the same character as a longer one that contains it. \
Where a family or group shares a name, a form that could denote more than one of them \
belongs to whichever the work actually uses it for, and if you cannot tell from the list \
alone, leave it in its own group rather than guessing.

Do not group two names merely because the people are related, allied, married, or often \
appear together. Group them only if they are the same person.

You may also be shown characters already recorded for this work. If a name in the list is \
another way of writing one of those, put it in a group and set same_as_registered to that \
recorded character's exact name. Otherwise leave same_as_registered empty.

A name that stands alone is a group of one. That is a normal answer.\
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["groups"],
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["canonical_name", "forms", "kind", "same_as_registered"],
                "properties": {
                    "canonical_name": {"type": "string"},
                    "forms": {"type": "array", "items": {"type": "string"}},
                    "kind": {
                        "type": "string",
                        "enum": ["person", "collective", "entity", "unknown"],
                    },
                    "same_as_registered": {"type": "string"},
                },
            },
        }
    },
}


class ResolutionError(Exception):
    """Resolution could not complete."""


def budget_for(form_count: int) -> int:
    """Output tokens to allow when grouping this many surface forms.

    Resolution is one call whose reply has to name every form it was given, so what it costs
    is set by the size of the cast rather than by the length of any passage. A fixed budget
    is therefore not merely too small at some size — it is the wrong shape, and will fail on
    a large enough work whatever number is chosen. This scales, and clamps.
    """
    if form_count < 0:
        raise ValueError("form_count cannot be negative")
    wanted = RESOLUTION_BASE_TOKENS + RESOLUTION_TOKENS_PER_FORM * form_count
    return min(wanted, RESOLUTION_MAX_TOKENS)


@dataclass(frozen=True)
class Candidate:
    """A surface form seen during extraction, and how often."""

    form: str
    occurrences: int = 0
    kinds: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        ranked = [kind for kind in self.kinds if kind != "unknown"]
        return ranked[0] if ranked else "unknown"


@dataclass(frozen=True)
class Resolution:
    """What resolution decided, and what it declined to decide."""

    assignments: dict[str, str] = field(default_factory=dict)
    """Surface form key to character identifier."""

    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    dropped_forms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    prompt_version: str | None = None
    model: str = ""
    provider: str = ""

    def character_for(self, form: str) -> str | None:
        return self.assignments.get(form_key(form))


def _gather(
    mentions: Iterable[MentionedCharacter],
) -> tuple[dict[str, Candidate], dict[str, set[str]], dict[str, str], list[str]]:
    """Collect surface forms, the primary names each alias was claimed by, and how each
    alias was written."""
    primaries: dict[str, Candidate] = {}
    alias_claims: dict[str, set[str]] = defaultdict(set)
    alias_forms: dict[str, str] = {}
    warnings: list[str] = []

    for mention in mentions:
        key = form_key(mention.name)
        if not key:
            continue
        existing = primaries.get(key)
        primaries[key] = Candidate(
            form=existing.form if existing else mention.name,
            occurrences=(existing.occurrences if existing else 0) + 1,
            kinds=((existing.kinds if existing else ()) + (mention.kind,)),
        )
        for alias in mention.aliases:
            alias_key = form_key(alias)
            if alias_key and alias_key != key:
                alias_claims[alias_key].add(key)
                alias_forms.setdefault(alias_key, alias)

    return primaries, dict(alias_claims), alias_forms, warnings


def _resolve_aliases(
    primaries: dict[str, Candidate],
    alias_claims: dict[str, set[str]],
    alias_forms: dict[str, str],
    assignments: dict[str, str],
) -> tuple[dict[str, tuple[str, str]], list[str], list[str]]:
    """Keep only aliases that unambiguously denote one character.

    **Ambiguity is judged against characters, not against the names that proposed them**,
    which is why this runs after grouping rather than before. Beforehand, "Elizabeth",
    "Elizabeth Bennet", and "Eliza Bennet" are three claimants; afterwards they are one
    character, and a form all three proposed is unanimous rather than contested. Judged too
    early, the most common familiar form for the protagonist is discarded precisely because
    everybody agreed on it.

    A form claimed by two genuinely different characters is still dropped, and that is what
    removes pronouns and generic epithets without a stop-list: they go because they are
    contested, not because they are on a list of words some language happens to use that way.
    ``assignments`` maps a name's form key to the character it resolved to, so "contested"
    now means what it says.
    """
    kept: dict[str, tuple[str, str]] = {}
    dropped: list[str] = []
    warnings: list[str] = []

    for alias_key, claimants in sorted(alias_claims.items()):
        if alias_key in primaries:
            dropped.append(alias_key)
            warnings.append(
                f"the form {alias_key!r} is a character's own name elsewhere in the work; "
                "not treating it as an alias"
            )
            continue

        owners = {assignments[claimant] for claimant in claimants if claimant in assignments}
        if not owners:
            # Every name that proposed it failed to resolve; there is nobody to attach to.
            dropped.append(alias_key)
            continue
        if len(owners) > 1:
            dropped.append(alias_key)
            names = ", ".join(sorted(primaries[c].form for c in claimants if c in primaries))
            warnings.append(
                f"the form {alias_key!r} was proposed as an alias of more than one "
                f"character ({names}); dropped as ambiguous"
            )
            continue
        kept[alias_key] = (next(iter(owners)), alias_forms.get(alias_key, alias_key))

    return kept, dropped, warnings


def _group_without_a_model(primaries: dict[str, Candidate]) -> list[dict[str, Any]]:
    """Every distinct name is its own character.

    The deterministic baseline. It never merges two forms that a reader would call one
    character, but it also never merges two that a reader would not — which makes it a
    usable floor rather than a broken fallback.
    """
    return [
        {
            "canonical_name": candidate.form,
            "forms": [candidate.form],
            "kind": candidate.kind,
            "same_as_registered": "",
        }
        for candidate in primaries.values()
    ]


def _group_with_a_model(
    primaries: dict[str, Candidate],
    registered: list[RegisteredCharacter],
    provider: Provider,
    *,
    max_tokens: int,
    effort: str | None,
) -> tuple[list[dict[str, Any]], str, str]:
    lines = [
        f"- {candidate.form} (seen in {candidate.occurrences} passage(s))"
        for candidate in sorted(
            primaries.values(), key=lambda c: (-c.occurrences, c.form.casefold())
        )
    ]
    prompt = "Names found in the work:\n" + "\n".join(lines)

    if registered:
        known = "\n".join(
            f"- {character.name}"
            + (f" (also written: {', '.join(character.aliases)})" if character.aliases else "")
            for character in registered
        )
        prompt += "\n\nCharacters already recorded for this work:\n" + known

    request = ModelRequest(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        max_tokens=max_tokens,
        effort=effort,
        output_schema=RESPONSE_SCHEMA,
        metadata={"step": "resolve"},
    )

    try:
        response = provider.complete(request)
    except ProviderError as error:
        raise ResolutionError(f"resolution failed: {error}") from error

    if response.refused:
        raise ResolutionError(
            "the provider declined to resolve names. A refusal is not an empty grouping."
        )

    try:
        payload = response.json()
    except ProviderError as error:
        raise ResolutionError(f"resolution failed: {error}") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ResolutionError("resolution reply did not contain a list of groups")

    return payload["groups"], response.model, response.provider


def resolve(
    extraction: Extraction,
    store: Store,
    collection_id: str,
    *,
    provider: Provider | None = None,
    max_tokens: int | None = None,
    effort: str | None = "medium",
) -> Resolution:
    """Resolve an extraction's surface forms into the collection's registry.

    Passing no provider uses the deterministic baseline, in which every distinct name is
    its own character. That is the honest floor: it under-merges rather than guessing.

    ``max_tokens`` defaults to a budget derived from how many forms are actually being
    grouped (``budget_for``); pass a number to override it.
    """
    return resolve_mentions(
        extraction.characters,
        store,
        collection_id,
        provider=provider,
        max_tokens=max_tokens,
        effort=effort,
    )


def resolve_mentions(
    mentions: Iterable[MentionedCharacter],
    store: Store,
    collection_id: str,
    *,
    provider: Provider | None = None,
    max_tokens: int | None = None,
    effort: str | None = "medium",
) -> Resolution:
    """Resolve surface forms from any number of reading passes into one registry.

    Separate from ``resolve`` because **4.3** reads narrative and reference material with
    different prompts and must resolve *both together*. "Ada" in the bible and "Ada" on the
    page have to become the same character, or the overlay 4.4 builds would compare a
    declaration against an enactment that never meets it — and every relation would look
    both undeclared and unenacted.
    """
    primaries, alias_claims, alias_forms, warnings = _gather(mentions)
    if not primaries:
        return Resolution(prompt_version=None, warnings=tuple(warnings))

    registered = store.list_characters(collection_id)
    by_form: dict[str, RegisteredCharacter] = {}
    for character in registered:
        for form in character.surface_forms:
            by_form[form_key(form)] = character

    # Names the registry already knows resolve to it, whatever this run would have called
    # them. This is what keeps identifiers stable across runs.
    assignments: dict[str, str] = {}
    unresolved: dict[str, Candidate] = {}
    for key, candidate in primaries.items():
        known = by_form.get(key)
        if known is not None:
            assignments[key] = known.id
        else:
            unresolved[key] = candidate

    model = provider_name = ""
    prompt_version: str | None = None
    groups: list[dict[str, Any]]
    if not unresolved:
        groups = []
    elif provider is None:
        groups = _group_without_a_model(unresolved)
    else:
        # Sized from the forms this call actually has to name, not from a constant: only
        # `unresolved` reaches the model, since anything the registry already claims was
        # answered above without asking.
        groups, model, provider_name = _group_with_a_model(
            unresolved,
            registered,
            provider,
            max_tokens=budget_for(len(unresolved)) if max_tokens is None else max_tokens,
            effort=effort,
        )
        prompt_version = PROMPT_VERSION

    created: list[str] = []
    updated: list[str] = []
    claimed: dict[str, str] = {}  # surface forms claimed during this run

    # First pass: settle identity. Aliases proposed by `alias_claims` are deliberately not
    # consulted yet — deciding whether one of them is contested needs the character each
    # claimant resolved to, which is exactly what this pass produces.
    pending: list[tuple[RegisteredCharacter, list[str]]] = []

    for group in groups:
        if not isinstance(group, dict):
            warnings.append("discarded a non-object group")
            continue

        forms = [str(form).strip() for form in group.get("forms") or [] if str(form).strip()]
        keys = [form_key(form) for form in forms if form_key(form) in unresolved]
        if not keys:
            continue

        canonical = str(group.get("canonical_name") or "").strip() or unresolved[keys[0]].form
        kind = str(group.get("kind") or "unknown")
        same_as = str(group.get("same_as_registered") or "").strip()

        target = by_form.get(form_key(same_as)) if same_as else None
        if same_as and target is None:
            warnings.append(
                f"the group for {canonical!r} claims to match a recorded character "
                f"{same_as!r}, which is not in the registry; treating it as new"
            )

        # `keys` only ever holds unresolved forms, so a group can never gather two
        # characters the registry already knows. That is what makes merging structurally
        # impossible here rather than merely guarded against.
        grouped = [unresolved[key].form for key in keys if key != form_key(canonical)]

        if target is not None:
            merged_aliases = tuple(
                dict.fromkeys([*target.aliases, *grouped, *(f for f in forms if f != target.name)])
            )
            character = RegisteredCharacter(
                id=target.id,
                collection_id=collection_id,
                name=target.name,
                kind=target.kind if target.kind != "unknown" else kind,
                provenance=target.provenance,
                review_status=target.review_status,
                aliases=tuple(a for a in merged_aliases if form_key(a) != form_key(target.name)),
            )
            updated.append(target.id)
        else:
            identifier = ids.character_id(canonical)
            if identifier in claimed.values():
                identifier = ids.character_id(canonical, disambiguator=keys[0][:8])
            character = RegisteredCharacter(
                id=identifier,
                collection_id=collection_id,
                name=canonical,
                kind=kind,
                provenance="observed",
                aliases=tuple(
                    dict.fromkeys(a for a in grouped if form_key(a) != form_key(canonical))
                ),
            )
            created.append(identifier)

        pending.append((character, keys))
        for form in character.surface_forms:
            assignments[form_key(form)] = character.id
            claimed[form_key(form)] = character.id
        for key in keys:
            assignments[key] = character.id

    # Second pass: now that every name has a character, a form several names proposed can be
    # judged on whether those names turned out to be one person.
    alias_owner, dropped, alias_warnings = _resolve_aliases(
        primaries, alias_claims, alias_forms, assignments
    )
    warnings.extend(alias_warnings)

    owned: dict[str, list[str]] = {}
    for owner_id, form in alias_owner.values():
        owned.setdefault(owner_id, []).append(form)

    for character, _keys in pending:
        extra = [
            form
            for form in owned.get(character.id, [])
            if form_key(form) != form_key(character.name)
        ]
        if extra:
            character = replace(
                character,
                aliases=tuple(dict.fromkeys([*character.aliases, *extra])),
            )
        store.upsert_character(character)
        for form in character.surface_forms:
            assignments[form_key(form)] = character.id

    # An alias whose owner the registry already knew has no pending record to attach to, so
    # its assignment is recorded directly.
    for alias_key, (owner_id, _form) in alias_owner.items():
        assignments.setdefault(alias_key, owner_id)

    return Resolution(
        assignments=assignments,
        created=tuple(created),
        updated=tuple(updated),
        dropped_forms=tuple(dropped),
        warnings=tuple(warnings),
        prompt_version=prompt_version,
        model=model,
        provider=provider_name,
    )

"""Extracting characters and interactions from a segmented text.

A whole work does not fit in one request, and asking for a graph in a single call
produces confident invention. So extraction is map-reduce: the text is divided into
windows of contiguous segments, each window is read independently, and the findings are
combined later.

This module is the *map* half only. It emits what each window says, using the names as
they appear on the page — no alias resolution (phase 1.5), no weights (phase 1.6), no
rejection of unverifiable quotations (phase 1.7). Keeping those apart matters: a pipeline
that resolves and aggregates while reading has no stage at which the raw observations can
be checked against the text.

Every interaction carries a quotation, and the model is told the quotation must be copied
from the passage rather than composed. That instruction is not trusted — phase 1.7
verifies it — but asking for something checkable is what makes checking possible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from typing import Any

from dramatis.providers import ModelRequest, ModelResponse, Provider, ProviderError
from dramatis.segmentation import Segmentation
from dramatis.store import DEFAULT_COLLECTIVES_ARE_ACTORS
from dramatis.text import normalise_whitespace

PROMPT_VERSION = "extract-v2"
"""A label a human maintains, bumped when the prompt or schema changes meaningfully.

It answers "what changed, in words a person can use". It cannot answer "was this the same
prompt", because a file anyone may edit can differ while the label stays put — that is what
``prompt_sha256`` is for (D18). Both are recorded because they answer different questions.

v2 excludes indefinite referents and, by default, collectives — see D19 and the first live
run, which made "the Netherfield party" a character beside the people in it.
"""

PROMPT_PACKAGE = "dramatis.prompts"
PROMPT_FILE = "extract.md"
COLLECTIVES_FILE = "extract-collectives.md"

DEFAULT_WINDOW_CHARACTERS = 12_000
"""Target window size. Windows never split a segment, so a single long segment may exceed
this; the budget is a target, not a ceiling."""


@cache
def _prompt_file(name: str) -> str:
    """Read one prompt file from the package.

    Nothing is stripped, templated, or reflowed on the way out: a file's bytes are its text.
    Prompts ship inside the package rather than beside the repository because the wheel
    contains only ``src/dramatis`` — anywhere else they would be present in a checkout and
    missing from an install.
    """
    try:
        return files(PROMPT_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as error:
        raise ExtractionError(
            f"the extraction prompt {PROMPT_PACKAGE}.{name} is missing from the "
            "installation. Reinstall dramatis; analysis cannot proceed without it."
        ) from error


def system_prompt(*, collectives_are_actors: bool = DEFAULT_COLLECTIVES_ARE_ACTORS) -> str:
    """The extraction prompt for a project studied under these terms.

    Composed rather than read whole, because one setting varies (D19). The base file is a
    complete prompt on its own and describes the default; enabling collectives appends a
    section that says which instruction it replaces. Two files rather than two copies: the
    alternative duplicates the ninety per cent they share and lets the copies drift.

    The hash recorded by a run covers what this returns, not either file, so the composition
    is inside the guarantee rather than beside it.
    """
    prompt = _prompt_file(PROMPT_FILE)
    if collectives_are_actors:
        prompt = f"{prompt.rstrip()}\n\n{_prompt_file(COLLECTIVES_FILE)}"
    return prompt


def prompt_sha256(*, collectives_are_actors: bool = DEFAULT_COLLECTIVES_ARE_ACTORS) -> str:
    """Hash of the prompt text actually sent.

    Recorded in every run so that two analyses claiming one ``PROMPT_VERSION`` but produced
    under different instructions are known to be incomparable rather than assumed alike.
    """
    text = system_prompt(collectives_are_actors=collectives_are_actors)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["characters", "interactions"],
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
        "interactions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["participants", "quotation", "note"],
                "properties": {
                    "participants": {"type": "array", "items": {"type": "string"}},
                    "quotation": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
    },
}


class ExtractionError(Exception):
    """A window could not be extracted. The message names the window."""


@dataclass(frozen=True)
class Window:
    """A contiguous run of segments read as one unit."""

    index: int
    start: int
    end: int
    segment_positions: tuple[int, ...]

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class MentionedCharacter:
    """A character as this window named it. Not yet resolved to a registry entry."""

    name: str
    aliases: tuple[str, ...] = ()
    kind: str = "unknown"


@dataclass(frozen=True)
class ObservedInteraction:
    """One pair shown in contact, with the passage that shows it.

    ``participants`` holds names exactly as the window reported them. Mapping those onto
    stable characters is phase 1.5's job, and doing it here would discard the surface form
    that resolution needs.
    """

    participants: tuple[str, str]
    quotation: str
    note: str | None = None
    segment_position: int | None = None
    """Which segment the quotation was found in, when it could be located. ``None`` means
    the quotation was not found in the window — recorded, not rejected; phase 1.7 decides."""


@dataclass(frozen=True)
class WindowFinding:
    window: Window
    characters: tuple[MentionedCharacter, ...] = ()
    interactions: tuple[ObservedInteraction, ...] = ()


@dataclass(frozen=True)
class Extraction:
    """Everything the map stage observed, plus what the run needs to be citable."""

    findings: tuple[WindowFinding, ...]
    prompt_version: str
    model: str
    provider: str
    prompt_sha256: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def characters(self) -> list[MentionedCharacter]:
        return [character for finding in self.findings for character in finding.characters]

    @property
    def interactions(self) -> list[ObservedInteraction]:
        return [interaction for finding in self.findings for interaction in finding.interactions]


def build_windows(
    segmentation: Segmentation, *, target_characters: int = DEFAULT_WINDOW_CHARACTERS
) -> list[Window]:
    """Group leaf segments into windows of roughly ``target_characters``.

    A segment is never split across windows. Splitting one would put half a sentence at a
    window boundary, and a quotation spanning the join could not be found in either half.
    """
    if target_characters <= 0:
        raise ValueError("target_characters must be positive")

    leaves = segmentation.leaves()
    windows: list[Window] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        first, last = segmentation.segments[current[0]], segmentation.segments[current[-1]]
        windows.append(
            Window(
                index=len(windows),
                start=first.start,
                end=last.end,
                segment_positions=tuple(current),
            )
        )
        current.clear()

    for position in leaves:
        segment = segmentation.segments[position]
        pending = segmentation.segments[current[0]].start if current else segment.start
        if current and (segment.end - pending) > target_characters:
            flush()
        current.append(position)

    flush()
    return windows


def _window_text(segmentation: Segmentation, window: Window) -> str:
    return segmentation.text[window.start : window.end]


def _locate(segmentation: Segmentation, window: Window, quotation: str) -> int | None:
    """Return the segment a quotation falls in, or None if it is not in the window.

    Matching is whitespace-normalised, for the reason set out in ``dramatis.text``: the
    source is hard-wrapped and a quoted sentence spans line breaks belonging to layout.
    """
    needle = normalise_whitespace(quotation)
    if not needle:
        return None
    for position in window.segment_positions:
        segment = segmentation.segments[position]
        haystack = normalise_whitespace(segmentation.text[segment.start : segment.end])
        if needle in haystack:
            return position
    return None


def _parse(
    payload: Any, window: Window
) -> tuple[tuple[MentionedCharacter, ...], tuple[ObservedInteraction, ...], list[str]]:
    if not isinstance(payload, dict):
        raise ExtractionError(f"window {window.index}: expected a JSON object, got {type(payload)}")

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
            MentionedCharacter(
                name=name,
                aliases=aliases,
                kind=str(entry.get("kind") or "unknown"),
            )
        )

    interactions: list[ObservedInteraction] = []
    for entry in payload.get("interactions") or []:
        if not isinstance(entry, dict):
            warnings.append(f"window {window.index}: discarded a non-object interaction entry")
            continue
        participants = [
            str(name).strip()
            for name in entry.get("participants") or []
            if isinstance(name, str) and str(name).strip()
        ]
        # The schema cannot express "exactly two" — array length constraints are not
        # supported — so it is enforced here rather than left to the provider.
        if len(participants) != 2:
            warnings.append(
                f"window {window.index}: discarded an interaction with {len(participants)} "
                "participant(s); an interaction joins exactly two"
            )
            continue
        if participants[0] == participants[1]:
            warnings.append(
                f"window {window.index}: discarded a self-interaction for {participants[0]!r}"
            )
            continue
        quotation = str(entry.get("quotation") or "")
        if not quotation.strip():
            warnings.append(
                f"window {window.index}: discarded an interaction between "
                f"{participants[0]!r} and {participants[1]!r} with no quotation"
            )
            continue
        note = str(entry.get("note") or "").strip() or None
        interactions.append(
            ObservedInteraction(
                participants=(participants[0], participants[1]),
                quotation=quotation,
                note=note,
            )
        )

    return tuple(characters), tuple(interactions), warnings


def extract(
    segmentation: Segmentation,
    provider: Provider,
    *,
    target_characters: int = DEFAULT_WINDOW_CHARACTERS,
    max_tokens: int = 8192,
    effort: str | None = "medium",
    collectives_are_actors: bool = DEFAULT_COLLECTIVES_ARE_ACTORS,
) -> Extraction:
    """Read every window and return what each one reported.

    Windows are read in order and independently. Nothing is resolved, weighted, or
    rejected here.
    """
    windows = build_windows(segmentation, target_characters=target_characters)

    # Composed once: every window is read under the same instructions, and the hash recorded
    # by the run is the hash of this string.
    prompt = system_prompt(collectives_are_actors=collectives_are_actors)

    findings: list[WindowFinding] = []
    warnings: list[str] = []
    input_tokens = output_tokens = 0
    model = provider_name = ""

    for window in windows:
        text = _window_text(segmentation, window)
        if not text.strip():
            continue

        request = ModelRequest(
            prompt=text,
            system=prompt,
            max_tokens=max_tokens,
            effort=effort,
            output_schema=RESPONSE_SCHEMA,
            metadata={"step": "extract", "window": str(window.index)},
        )

        try:
            response: ModelResponse = provider.complete(request)
        except ProviderError as error:
            raise ExtractionError(f"window {window.index}: {error}") from error

        if response.refused:
            raise ExtractionError(
                f"window {window.index}: the provider declined to answer. A refusal is not "
                "an empty result — the window was not read."
            )

        try:
            payload = response.json()
        except ProviderError as error:
            raise ExtractionError(f"window {window.index}: {error}") from error

        characters, interactions, window_warnings = _parse(payload, window)
        warnings.extend(window_warnings)

        located = tuple(
            ObservedInteraction(
                participants=interaction.participants,
                quotation=interaction.quotation,
                note=interaction.note,
                segment_position=_locate(segmentation, window, interaction.quotation),
            )
            for interaction in interactions
        )

        findings.append(WindowFinding(window=window, characters=characters, interactions=located))

        model = response.model or model
        provider_name = response.provider or provider_name
        input_tokens += response.input_tokens or 0
        output_tokens += response.output_tokens or 0

    return Extraction(
        findings=tuple(findings),
        prompt_version=PROMPT_VERSION,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        model=model,
        provider=provider_name or getattr(provider, "name", "unknown"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        warnings=tuple(warnings),
    )

"""The analysis pipeline, end to end.

Segment, read, verify, resolve, aggregate, record. Each stage lives in its own module and
is tested there; this is the order they run in, and the place where the run metadata that
makes a result citable is assembled.

The order is not arbitrary. Verification sits between reading and aggregating so that
weights never count evidence that was later rejected, and resolution runs on the raw
extraction rather than the verified subset so that a character seen only in a passage whose
quotation failed is still registered — losing a quotation should not lose a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dramatis import __version__
from dramatis.aggregation import Aggregation, aggregate
from dramatis.assertion import (
    ASSERTED_STATEMENTS,
    Assertions,
    aggregate_assertions,
    extract_assertions,
)
from dramatis.correction import Application, record_conflicts
from dramatis.correction import apply as apply_corrections
from dramatis.extraction import DEFAULT_WINDOW_CHARACTERS, Extraction, extract
from dramatis.extraction import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from dramatis.providers import Provider
from dramatis.resolution import PROMPT_VERSION as RESOLUTION_PROMPT_VERSION
from dramatis.resolution import Resolution, resolve_mentions
from dramatis.segmentation import SegmentationSpec, segment_text
from dramatis.snapshot import AnalysisRun, build_document, save_snapshot
from dramatis.store import (
    COLLECTIVES_ARE_ACTORS,
    DEFAULT_COLLECTIVES_ARE_ACTORS,
    NARRATIVE,
    REFERENCE,
    Store,
    StoredSnapshot,
    utc_now,
)
from dramatis.verification import (
    DEFAULT_MAX_REJECTION_RATE,
    Verification,
    verify,
)

PIPELINE_VERSION = "pipeline-v1"


class PipelineError(Exception):
    """The pipeline could not run."""


@dataclass(frozen=True)
class AnalysisResult:
    """Everything the run produced, so a caller can report on it without re-deriving it."""

    snapshot: StoredSnapshot
    extraction: Extraction
    verification: Verification
    resolution: Resolution
    aggregation: Aggregation
    assertions: Assertions = field(default_factory=Assertions)
    """What the reference documents declared. Empty when the revision has none."""
    asserted: Aggregation = field(
        default_factory=lambda: Aggregation(weight_basis=ASSERTED_STATEMENTS)
    )
    """Asserted relations, kept apart from `aggregation` rather than merged into it.

    The two carry different weight bases, and `require_comparable` exists to stop anything
    ranking or diffing them together. Holding them in one field would have thrown that away
    at the last moment.
    """

    corrections: Application = field(default_factory=lambda: Application(document={}))
    """What human corrections did to this reading (**5.2**): which were applied, which the
    reading disagreed with, and which no longer have a subject to attach to."""

    @property
    def warnings(self) -> tuple[str, ...]:
        return (
            self.extraction.warnings
            + self.assertions.warnings
            + self.resolution.warnings
            + self.aggregation.warnings
            + self.asserted.warnings
            + self.corrections.warnings
        )


def analyse(
    store: Store,
    text_revision_id: str,
    provider: Provider,
    *,
    resolution_provider: Provider | None = None,
    spec: SegmentationSpec | None = None,
    target_characters: int = DEFAULT_WINDOW_CHARACTERS,
    max_rejection_rate: float = DEFAULT_MAX_REJECTION_RATE,
    label: str | None = None,
    effort: str | None = "medium",
    now: str | None = None,
) -> AnalysisResult:
    """Analyse one text revision and record the result as an immutable snapshot."""
    revision = store.get_text_revision(text_revision_id)
    if revision is None:
        raise PipelineError(f"unknown text revision {text_revision_id!r}")

    work = store.get_work(revision.work_id)
    if work is None:
        raise PipelineError(f"revision {text_revision_id!r} belongs to a work that is gone")
    collection_id = str(work["collection_id"])

    # Narrative and reference material are read separately and never concatenated (4.3).
    # A bible read under the narrative prompt yields relations marked `observed`, which
    # claims the story enacted something the author only wrote down.
    text = store.revision_text(text_revision_id, roles=[NARRATIVE])
    reference_text = store.revision_text(text_revision_id, roles=[REFERENCE])
    if not text.strip() and not reference_text.strip():
        raise PipelineError(f"revision {text_revision_id!r} has no text")

    started_at = now or utc_now()

    segmentation = segment_text(text, spec)
    reference_segmentation = segment_text(reference_text, spec) if reference_text.strip() else None
    if spec is not None:
        store.upsert_work(
            work["id"],
            collection_id,
            work["title"],
            creator=work.get("creator"),
            language=work.get("language"),
            edition=work.get("edition"),
            segment_types=list(segmentation.segment_types),
        )

    # The terms the project is studied under, not an argument to this call (D17, D19). Two
    # analyses of one corpus must ask the same question unless somebody deliberately changed
    # it, and a per-call argument would let them differ by accident.
    collectives_are_actors = bool(
        store.get_setting(COLLECTIVES_ARE_ACTORS, DEFAULT_COLLECTIVES_ARE_ACTORS)
    )

    extraction = (
        extract(
            segmentation,
            provider,
            target_characters=target_characters,
            effort=effort,
            collectives_are_actors=collectives_are_actors,
        )
        if text.strip()
        else Extraction(
            findings=(), prompt_version=EXTRACTION_PROMPT_VERSION, model="", provider=""
        )
    )

    verification = verify(extraction, segmentation, max_rejection_rate=max_rejection_rate)

    assertions = Assertions()
    assertion_verification = Verification()
    if reference_segmentation is not None:
        assertions = extract_assertions(
            reference_segmentation,
            provider,
            target_characters=target_characters,
            effort=effort,
        )
        # The same gate, against the reference text. Invariant 3 does not soften because a
        # relation was declared rather than enacted.
        assertion_verification = verify(
            assertions.relationships,
            reference_segmentation,
            max_rejection_rate=max_rejection_rate,
        )

    # One resolution over both passes. "Ada" in the bible and "Ada" on the page must become
    # the same character, or 4.4's overlay would compare a declaration against an enactment
    # that never meets it, and every relation would read as both undeclared and unenacted.
    #
    # Run on the whole reading, not the verified subset: a character seen only in a passage
    # whose quotation failed is still a character in the work.
    resolution = resolve_mentions(
        extraction.characters + assertions.characters,
        store,
        collection_id,
        provider=resolution_provider if resolution_provider is not None else provider,
        effort=effort,
    )

    aggregation = aggregate(
        verification.verified,
        resolution,
        segmentation,
        # The spans, not a single id: a revision of a folder is many documents, and which
        # one a quotation belongs to is decided by where its passage falls. Narrowed to the
        # same roles as the text, or the offsets would index a different string.
        document_spans=store.revision_document_spans(text_revision_id, roles=[NARRATIVE]),
    )

    asserted = Aggregation(weight_basis=ASSERTED_STATEMENTS)
    if reference_segmentation is not None:
        asserted = aggregate_assertions(
            assertion_verification.verified,
            resolution,
            reference_segmentation,
            document_spans=store.revision_document_spans(text_revision_id, roles=[REFERENCE]),
        )

    # Parameters are what the run was *asked* to do, never what happened to it. The
    # distinction is not pedantry: these are the material a run's identity is hashed from,
    # so an outcome recorded here makes two analyses of one configuration into two
    # configurations, and a diff between them can then credit nothing to either axis.
    #
    # `resolution_prompt_version` was exactly that mistake. `Resolution.prompt_version` is
    # null when resolution answered from the registry without consulting a model, which
    # happens on every analysis after the first — so a second run of identical settings
    # recorded a different configuration from the first, for a reason that was about the
    # state of the registry rather than about the analysis. It records the version this run
    # was configured to use, which it was configured to use whether or not it needed it.
    parameters: dict[str, Any] = {
        "effort": effort,
        "target_characters": target_characters,
        "max_rejection_rate": max_rejection_rate,
        "segment_types": list(segmentation.segment_types),
        "resolution_prompt_version": RESOLUTION_PROMPT_VERSION,
        "weight_basis": aggregation.weight_basis,
        COLLECTIVES_ARE_ACTORS: collectives_are_actors,
    }
    # Recorded only when there was reference material to read. A run's identity is hashed
    # from these, so adding a key unconditionally would give every narrative-only corpus a
    # new run identifier for a question it was never asked.
    if reference_segmentation is not None:
        parameters["assertion_prompt_version"] = assertions.prompt_version
        parameters["asserted_weight_basis"] = asserted.weight_basis

    run = AnalysisRun(
        model=extraction.model or assertions.model or "none",
        provider=extraction.provider or assertions.provider or None,
        prompt_version=extraction.prompt_version,
        prompt_sha256=extraction.prompt_sha256 or None,
        pipeline_version=PIPELINE_VERSION,
        application_version=__version__,
        parameters=parameters,
        started_at=started_at,
        completed_at=now or utc_now(),
    )

    character_ids = set(resolution.assignments.values())
    for relation in aggregation.relations + asserted.relations:
        character_ids.update({relation.source, relation.target})

    document = build_document(
        store,
        work_id=revision.work_id,
        text_revision_id=text_revision_id,
        run=run,
        character_ids=character_ids,
        aggregation=aggregation,
        asserted=asserted,
        label=label,
        created_at=now,
    )

    # Human corrections are written in here, between rendering and storing (**5.2**). This is
    # the point of the bullet: a person's edit survives re-analysis because every snapshot
    # built after it is built with it. Applying before `save_snapshot` also means the
    # corrected document is what the schema is asked to validate, so a correction that would
    # produce an invalid graph fails here rather than being discovered by a reader.
    application = apply_corrections(store, document)

    stored = save_snapshot(store, application.document)
    record_conflicts(
        store,
        work_id=revision.work_id,
        snapshot_id=stored.id,
        conflicts=application.conflicts,
    )
    return AnalysisResult(
        snapshot=stored,
        extraction=extraction,
        verification=verification,
        resolution=resolution,
        aggregation=aggregation,
        assertions=assertions,
        asserted=asserted,
        corrections=application,
    )

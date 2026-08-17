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

from dataclasses import dataclass
from typing import Any

from dramatis import __version__
from dramatis.aggregation import Aggregation, aggregate
from dramatis.extraction import DEFAULT_WINDOW_CHARACTERS, Extraction, extract
from dramatis.providers import Provider
from dramatis.resolution import Resolution, resolve
from dramatis.segmentation import SegmentationSpec, segment_text
from dramatis.snapshot import AnalysisRun, build_document, save_snapshot
from dramatis.store import (
    COLLECTIVES_ARE_ACTORS,
    DEFAULT_COLLECTIVES_ARE_ACTORS,
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

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.extraction.warnings + self.resolution.warnings + self.aggregation.warnings


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

    text = store.revision_text(text_revision_id)
    if not text.strip():
        raise PipelineError(f"revision {text_revision_id!r} has no text")

    started_at = now or utc_now()

    segmentation = segment_text(text, spec)
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

    extraction = extract(
        segmentation,
        provider,
        target_characters=target_characters,
        effort=effort,
        collectives_are_actors=collectives_are_actors,
    )

    verification = verify(extraction, segmentation, max_rejection_rate=max_rejection_rate)

    # Resolution runs on the whole extraction, not the verified subset: a character seen
    # only in a passage whose quotation failed is still a character in the work.
    resolution = resolve(
        extraction,
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
        # one a quotation belongs to is decided by where its passage falls.
        document_spans=store.revision_document_spans(text_revision_id),
    )

    parameters: dict[str, Any] = {
        "effort": effort,
        "target_characters": target_characters,
        "max_rejection_rate": max_rejection_rate,
        "segment_types": list(segmentation.segment_types),
        "resolution_prompt_version": resolution.prompt_version,
        "weight_basis": aggregation.weight_basis,
        COLLECTIVES_ARE_ACTORS: collectives_are_actors,
    }

    run = AnalysisRun(
        model=extraction.model or "none",
        provider=extraction.provider or None,
        prompt_version=extraction.prompt_version,
        prompt_sha256=extraction.prompt_sha256 or None,
        pipeline_version=PIPELINE_VERSION,
        application_version=__version__,
        parameters=parameters,
        started_at=started_at,
        completed_at=now or utc_now(),
    )

    character_ids = set(resolution.assignments.values())
    for relation in aggregation.relations:
        character_ids.update({relation.source, relation.target})

    document = build_document(
        store,
        work_id=revision.work_id,
        text_revision_id=text_revision_id,
        run=run,
        character_ids=character_ids,
        aggregation=aggregation,
        label=label,
        created_at=now,
    )

    stored = save_snapshot(store, document)
    return AnalysisResult(
        snapshot=stored,
        extraction=extraction,
        verification=verification,
        resolution=resolution,
        aggregation=aggregation,
    )

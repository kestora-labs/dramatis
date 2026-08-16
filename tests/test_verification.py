"""Tests for the verbatim verification gate.

Invariant 3 says failures are rejected, not warned about. The tests that matter are
therefore the ones checking that an unverifiable quotation is *absent* from the output —
not merely mentioned somewhere — and that a run losing too much of what it found refuses
rather than returning a thin graph that looks fine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.aggregation import aggregate
from dramatis.extraction import (
    Extraction,
    MentionedCharacter,
    ObservedInteraction,
    Window,
    WindowFinding,
)
from dramatis.resolution import Resolution
from dramatis.segmentation import segment_text
from dramatis.store import form_key
from dramatis.text import contains_quotation
from dramatis.verification import (
    DEFAULT_MAX_REJECTION_RATE,
    Verification,
    VerificationError,
    count_occurrences,
    verify,
)

FIXTURE_A = Path(__file__).resolve().parents[1] / "fixtures" / "a"

PASSAGE = (
    "Ada met Bram at the gate.\n"
    "\n"
    "In vain have I\nstruggled. It will not do.\n"
    "\n"
    "Later, Cai spoke to Ada alone.\n"
)


@pytest.fixture
def segmentation():
    return segment_text(PASSAGE)


def interaction(quotation: str, *, position: int | None = 0) -> ObservedInteraction:
    return ObservedInteraction(
        participants=("Ada", "Bram"), quotation=quotation, segment_position=position
    )


def many(quotation: str, count: int, *, position: int | None = 0) -> list[ObservedInteraction]:
    return [interaction(quotation, position=position) for _ in range(count)]


# -- the gate ---------------------------------------------------------------------------------


class TestVerifying:
    def test_a_real_quotation_passes(self, segmentation) -> None:
        result = verify([interaction("Ada met Bram at the gate.")], segmentation)

        assert len(result) == 1 and result.rejected == 0

    def test_an_invented_quotation_is_removed_not_flagged(self, segmentation) -> None:
        """Invariant 3: rejected, not surfaced with a warning."""
        result = verify([interaction("Ada embraced Bram warmly.")], segmentation)

        assert result.verified == (), "the invention survived into the output"
        assert result.rejected == 1
        assert "not in the source text" in result.rejections[0].reason

    def test_the_rejection_quotes_what_was_rejected(self, segmentation) -> None:
        result = verify([interaction("Ada embraced Bram warmly.")], segmentation)

        assert "Ada embraced Bram warmly." in result.rejections[0].reason
        assert "Ada" in str(result.rejections[0])

    def test_an_empty_quotation_is_rejected(self, segmentation) -> None:
        result = verify([interaction("   ")], segmentation)

        assert result.verified == ()
        assert "empty" in result.rejections[0].reason

    def test_a_line_wrapped_quotation_passes(self, segmentation) -> None:
        """The source is hard-wrapped; line breaks belong to layout, not the work."""
        result = verify([interaction("In vain have I struggled.", position=1)], segmentation)

        assert len(result) == 1

    def test_case_differences_do_not_pass(self, segmentation) -> None:
        """Only whitespace is relaxed. A changed word is a changed quotation."""
        result = verify([interaction("ada met bram at the gate.")], segmentation)

        assert result.verified == ()

    def test_a_near_miss_does_not_pass(self, segmentation) -> None:
        result = verify([interaction("Ada met Bram at the gates.")], segmentation)

        assert result.verified == ()

    def test_good_and_bad_are_separated(self, segmentation) -> None:
        result = verify(
            [
                interaction("Ada met Bram at the gate."),
                interaction("Something nobody wrote."),
                interaction("Later, Cai spoke to Ada alone.", position=2),
            ],
            segmentation,
        )

        assert len(result) == 2 and result.rejected == 1
        assert all(contains_quotation(PASSAGE, kept.quotation) for kept in result.verified)

    def test_nothing_to_check_is_not_a_failure(self, segmentation) -> None:
        result = verify([], segmentation)

        assert len(result) == 0 and result.rejection_rate == 0.0

    def test_an_extraction_can_be_passed_directly(self, segmentation) -> None:
        finding = WindowFinding(
            window=Window(index=0, start=0, end=len(PASSAGE), segment_positions=(0, 1, 2)),
            characters=(MentionedCharacter("Ada"),),
            interactions=(interaction("Ada met Bram at the gate."), interaction("Invented.")),
        )
        extraction = Extraction(
            findings=(finding,), prompt_version="extract-v1", model="m", provider="p"
        )

        result = verify(extraction, segmentation)

        assert len(result) == 1 and result.rejected == 1


# -- locators -----------------------------------------------------------------------------------


class TestLocators:
    def test_a_correctly_placed_quotation_keeps_its_position(self, segmentation) -> None:
        result = verify([interaction("Later, Cai spoke to Ada alone.", position=2)], segmentation)

        assert result.verified[0].segment_position == 2
        assert result.relocated == 0

    def test_a_real_quotation_at_the_wrong_address_is_relocated(self, segmentation) -> None:
        """Evidence with a bad locator is still evidence — a different fault from invention."""
        result = verify([interaction("Later, Cai spoke to Ada alone.", position=0)], segmentation)

        assert len(result) == 1 and result.rejected == 0
        assert result.verified[0].segment_position == 2
        assert result.relocated == 1

    def test_a_quotation_with_no_address_gets_one(self, segmentation) -> None:
        result = verify(
            [interaction("Later, Cai spoke to Ada alone.", position=None)], segmentation
        )

        assert result.verified[0].segment_position == 2
        assert result.relocated == 1

    def test_a_quotation_spanning_a_break_is_kept_and_attributed_to_its_start(
        self, segmentation
    ) -> None:
        """A line and the reply it draws are one span of the work, though two passages.

        Rejecting these was the first implementation, and the real fixture caught it: two
        of its hand-verified quotations run from narration into the speech that answers it.
        They are verbatim in the work, so refusing them would have been the gate lying.
        """
        straddling = "at the gate. In vain have I struggled."
        assert contains_quotation(PASSAGE, straddling), "the fixture should contain this span"

        result = verify([interaction(straddling, position=None)], segmentation)

        assert len(result) == 1 and result.rejected == 0
        assert result.verified[0].segment_position == 0, "attributed where it begins"

    def test_ambiguity_is_reportable_without_being_fatal(self) -> None:
        text = "She said no.\n\nHe left.\n\nShe said no.\n"
        segmentation = segment_text(text)

        assert count_occurrences(segmentation, "She said no.") == 2
        result = verify([interaction("She said no.", position=0)], segmentation)
        assert len(result) == 1


# -- the circuit breaker ---------------------------------------------------------------------------


class TestTheCircuitBreaker:
    def test_a_run_losing_too_much_is_refused(self, segmentation) -> None:
        """A thin graph looks plausible and leaves no trace of what went missing."""
        interactions = many("Ada met Bram at the gate.", 2) + many("Pure invention.", 8)

        with pytest.raises(VerificationError, match="refused"):
            verify(interactions, segmentation)

    def test_the_error_gives_the_numbers(self, segmentation) -> None:
        interactions = many("Ada met Bram at the gate.", 2) + many("Pure invention.", 8)

        with pytest.raises(VerificationError) as failure:
            verify(interactions, segmentation)

        assert "8 of 10" in str(failure.value)
        assert "80%" in str(failure.value)

    def test_a_few_failures_do_not_refuse_the_run(self, segmentation) -> None:
        interactions = many("Ada met Bram at the gate.", 9) + many("Pure invention.", 1)

        result = verify(interactions, segmentation)

        assert len(result) == 9 and result.rejected == 1

    def test_the_threshold_is_configurable(self, segmentation) -> None:
        interactions = many("Ada met Bram at the gate.", 9) + many("Pure invention.", 1)

        with pytest.raises(VerificationError):
            verify(interactions, segmentation, max_rejection_rate=0.0)

    def test_a_small_sample_does_not_trip_it(self, segmentation) -> None:
        """One failure out of two is 50%, but two quotations say nothing about a run."""
        result = verify(
            [interaction("Ada met Bram at the gate."), interaction("Invented.")], segmentation
        )

        assert result.rejected == 1
        assert result.rejection_rate == 0.5, "the rate is still reported"

    def test_the_default_threshold_is_a_smoke_alarm_not_a_target(self) -> None:
        assert 0.1 <= DEFAULT_MAX_REJECTION_RATE <= 0.4

    def test_rejections_are_not_warnings(self) -> None:
        """Invariant 3 distinguishes them, so the type must too."""
        assert not hasattr(Verification, "warnings")
        assert "rejections" in Verification.__dataclass_fields__


# -- placement in the pipeline ---------------------------------------------------------------------


class TestBeforeAggregation:
    def test_verification_runs_before_weights_are_computed(self, segmentation) -> None:
        """Filtering after aggregation would leave weights counting rejected evidence."""
        resolution = Resolution(
            assignments={form_key("Ada"): "char:ada", form_key("Bram"): "char:bram"}
        )
        interactions = [
            interaction("Ada met Bram at the gate.", position=0),
            interaction("An invented line.", position=0),
            interaction("Later, Cai spoke to Ada alone.", position=2),
        ]

        unfiltered = aggregate(interactions, resolution, segmentation)
        filtered = aggregate(verify(interactions, segmentation).verified, resolution, segmentation)

        assert unfiltered.relations[0].weight == 2
        assert filtered.relations[0].weight == 2, "distinct passages, so the invention shared one"
        assert len(filtered.relations[0].evidence) == 2

    def test_a_rejected_quotation_never_reaches_the_evidence(self, segmentation) -> None:
        resolution = Resolution(
            assignments={form_key("Ada"): "char:ada", form_key("Bram"): "char:bram"}
        )
        interactions = [
            interaction("Ada met Bram at the gate.", position=0),
            interaction("An invented line.", position=1),
        ]

        result = aggregate(verify(interactions, segmentation).verified, resolution, segmentation)
        quotations = [piece.quotation for piece in result.relations[0].evidence]

        assert quotations == ["Ada met Bram at the gate."]

    def test_relocation_corrects_the_locator_that_reaches_evidence(self, segmentation) -> None:
        resolution = Resolution(
            assignments={form_key("Ada"): "char:ada", form_key("Bram"): "char:bram"}
        )
        misplaced = [interaction("Later, Cai spoke to Ada alone.", position=0)]

        result = aggregate(verify(misplaced, segmentation).verified, resolution, segmentation)
        path = result.relations[0].evidence[0].locator["path"]

        assert path[0]["index"] == 3, "the third passage, not the first it claimed"


# -- against the real fixture ------------------------------------------------------------------


class TestAgainstTheRealFixture:
    def test_the_hand_authored_fixture_passes_its_own_gate(self) -> None:
        """Two independent implementations of 'verbatim' agreeing is worth more than one."""
        source = (FIXTURE_A / "source" / "pride-and-prejudice.txt").read_text(encoding="utf-8")
        snapshot = json.loads((FIXTURE_A / "snapshot.json").read_text(encoding="utf-8"))
        segmentation = segment_text(source)

        quotations = [
            piece["selector"]["exact"]
            for relation in snapshot["relations"]
            for piece in relation.get("evidence", [])
        ]
        assert len(quotations) >= 10, "the fixture should carry real evidence"

        result = verify(
            [interaction(quotation, position=None) for quotation in quotations], segmentation
        )

        assert result.rejected == 0, [str(r) for r in result.rejections]
        assert len(result) == len(quotations)

    def test_a_tampered_fixture_quotation_is_caught(self) -> None:
        source = (FIXTURE_A / "source" / "pride-and-prejudice.txt").read_text(encoding="utf-8")
        segmentation = segment_text(source)

        result = verify(
            [interaction("In vain have I struggled hard.", position=None)], segmentation
        )

        assert result.rejected == 1

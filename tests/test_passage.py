"""Opening the source at a locator, with the quotation found inside it."""

from __future__ import annotations

import re

import pytest

from dramatis.passage import (
    PassageNotFound,
    StructureNotReproducible,
    find_passage,
    spec_for_types,
)
from dramatis.segmentation import SegmentationSpec, SegmentRule, segment_text

TEXT = """First block, which mentions Ada.

Second block, in which Ada
and Bram argue about the
weather at some length.

Third block, quiet.
"""


def a_segmentation():
    return segment_text(TEXT)


class TestFindingThePassage:
    def test_opens_the_passage_a_locator_names(self):
        found = find_passage(a_segmentation(), [{"type": "section", "index": 3}])
        assert found.text == "Third block, quiet."

    def test_locates_a_quotation_inside_the_passage(self):
        found = find_passage(a_segmentation(), [{"type": "section", "index": 1}], "mentions Ada")

        assert found.located
        assert found.text[found.start : found.end] == "mentions Ada"

    def test_finds_a_quotation_that_was_hard_wrapped_in_the_source(self):
        # The whole point of normalising: in the file this sentence is broken across three
        # lines, and a browser searching the raw text for it would find nothing.
        found = find_passage(
            a_segmentation(),
            [{"type": "section", "index": 2}],
            "Ada and Bram argue about the weather",
        )

        assert found.located
        assert found.text[found.start : found.end] == "Ada and Bram argue about the weather"

    def test_reports_a_quotation_it_cannot_find_rather_than_failing(self):
        # Showing the right passage without a highlight is more use than showing nothing.
        # Recovering the quotation itself is 2.4.
        found = find_passage(
            a_segmentation(), [{"type": "section", "index": 1}], "words nobody wrote"
        )

        assert not found.located
        assert found.start is None
        assert found.text == "First block, which mentions Ada."

    def test_returns_the_passage_when_no_quotation_is_asked_for(self):
        found = find_passage(a_segmentation(), [{"type": "section", "index": 1}])

        assert not found.located
        assert found.text.startswith("First block")

    def test_carries_the_document_through_untouched(self):
        found = find_passage(
            a_segmentation(), [{"type": "section", "index": 1}], document_id="doc:one"
        )
        assert found.document_id == "doc:one"

    def test_refuses_a_position_the_revision_does_not_have(self):
        with pytest.raises(PassageNotFound, match="section 99"):
            find_passage(a_segmentation(), [{"type": "section", "index": 99}])

    def test_refuses_a_locator_with_no_ordinal(self):
        with pytest.raises(PassageNotFound, match="no ordinal position"):
            find_passage(a_segmentation(), [{"type": "section"}])

    def test_refuses_an_empty_path(self):
        with pytest.raises(PassageNotFound):
            find_passage(a_segmentation(), [])

    def test_ignores_a_label_that_has_since_been_edited(self):
        # A stored path carries the label a segment had when it was written. Matching on it
        # would make a passage unreachable because somebody fixed a typo in a heading.
        found = find_passage(
            a_segmentation(), [{"type": "section", "index": 3, "label": "Some Old Name"}]
        )
        assert found.text == "Third block, quiet."


class TestQuotationsThatCrossAPassageBoundary:
    """`verification` attributes a quotation to the passage it *begins* in, so a quotation
    is not always contained by the passage its locator names."""

    def test_widens_the_window_until_it_holds_the_whole_quotation(self):
        found = find_passage(
            a_segmentation(),
            [{"type": "section", "index": 1}],
            "mentions Ada. Second block",
        )

        assert found.located
        assert found.widened
        assert found.text[found.start : found.end] == "mentions Ada. Second block"

    def test_says_when_the_window_had_to_grow(self):
        contained = find_passage(
            a_segmentation(), [{"type": "section", "index": 1}], "mentions Ada"
        )
        assert not contained.widened

    def test_stops_widening_once_the_quotation_could_not_still_fit(self):
        # Growing without a stopping rule would return the whole book for one bad quotation.
        found = find_passage(a_segmentation(), [{"type": "section", "index": 1}], "not here")

        assert not found.located
        assert found.text == "First block, which mentions Ada."

    def test_does_not_widen_backwards(self):
        # A quotation is attributed to where it begins, so the window only ever grows
        # forward. Growing back would silently re-attribute it to an earlier passage.
        found = find_passage(
            a_segmentation(), [{"type": "section", "index": 3}], "quiet. First block"
        )
        assert not found.located


class TestNestedStructure:
    def spec(self):
        return SegmentationSpec(
            (
                SegmentRule(type="part", pattern=re.compile(r"^PART .*$", re.MULTILINE)),
                SegmentRule(type="chapter", pattern=re.compile(r"^Chapter .*$", re.MULTILINE)),
            )
        )

    def text(self):
        return (
            "PART ONE\nChapter 1\nAlpha content.\nChapter 2\nBeta content.\n"
            "PART TWO\nChapter 1\nGamma content.\n"
        )

    def test_addresses_a_segment_by_its_whole_path(self):
        segmentation = segment_text(self.text(), self.spec())
        found = find_passage(
            segmentation,
            [{"type": "part", "index": 2}, {"type": "chapter", "index": 1}],
            "Gamma content.",
        )

        assert found.located
        assert "Gamma" in found.text

    def test_does_not_confuse_the_same_ordinal_under_a_different_parent(self):
        # Chapter 1 exists under both parts. A path is the whole chain, not the last step.
        segmentation = segment_text(self.text(), self.spec())
        first = find_passage(
            segmentation, [{"type": "part", "index": 1}, {"type": "chapter", "index": 1}]
        )

        assert "Alpha" in first.text
        assert "Gamma" not in first.text


class TestReproducingTheStructure:
    """A work stores the *names* of its segment types and never the rules that found them,
    so only the structure the names identify can be reproduced."""

    def test_a_work_that_never_overrode_the_default_is_reproducible(self):
        assert spec_for_types([]).segment_types == ["section"]
        assert spec_for_types(None).segment_types == ["section"]

    def test_a_work_declaring_the_default_type_is_reproducible(self):
        assert spec_for_types(["section"]).segment_types == ["section"]

    def test_refuses_a_nested_structure_whose_rules_were_never_stored(self):
        with pytest.raises(StructureNotReproducible, match="chapter › paragraph"):
            spec_for_types(["chapter", "paragraph"])

    def test_refuses_a_single_custom_type_rather_than_assuming_it_is_flat(self):
        # The dangerous case, and the reason this refuses more than it strictly must. A work
        # divided into chapters would have its blank-line blocks called chapters, and
        # "chapter 3" would open the third paragraph of the title page looking like it
        # worked. A refusal costs a feature; a confident wrong passage costs the reader
        # their trust in every other one.
        with pytest.raises(StructureNotReproducible, match="blank lines"):
            spec_for_types(["chapter"])

    def test_the_refusal_names_the_division_it_could_not_reproduce(self):
        with pytest.raises(StructureNotReproducible, match="divided into panel"):
            spec_for_types(["panel"])

"""Tests for segmentation.

The property under test throughout is Invariant 1: the module supplies a mechanism and no
vocabulary. Every segment type in these tests is invented by the test, and a nonsense
vocabulary must work exactly as well as a familiar one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dramatis.segmentation import (
    DEFAULT_SPEC,
    SegmentationError,
    SegmentationSpec,
    SegmentRule,
    segment_text,
)

FIXTURE_A = Path(__file__).resolve().parents[1] / "fixtures" / "a" / "source"


def rule(segment_type: str, pattern: str, **kwargs) -> SegmentRule:
    return SegmentRule(type=segment_type, pattern=re.compile(pattern, re.MULTILINE), **kwargs)


class TestDefaultSpec:
    def test_splits_on_blank_lines_into_flat_sections(self) -> None:
        result = segment_text("one\n\ntwo\n\nthree")

        assert [segment.type for segment in result] == ["section"] * 3
        assert [result.text_of(i).strip() for i in range(3)] == ["one", "two", "three"]

    def test_indices_count_from_one(self) -> None:
        result = segment_text("one\n\ntwo")
        assert [segment.index for segment in result] == [1, 2]

    def test_default_produces_no_hierarchy(self) -> None:
        """An unknown structure must not have one invented for it."""
        result = segment_text("one\n\ntwo\n\nthree")
        assert all(segment.parent is None for segment in result)
        assert all(segment.depth == 0 for segment in result)

    def test_text_without_blank_lines_is_one_section(self) -> None:
        result = segment_text("a single unbroken passage")
        assert len(result) == 1
        assert result.text_of(0) == "a single unbroken passage"

    def test_leading_blank_lines_do_not_create_an_empty_segment(self) -> None:
        result = segment_text("\n\n\nfirst\n\nsecond")
        assert len(result) == 2
        assert result.text_of(0).strip() == "first"


class TestCustomVocabulary:
    def test_types_are_whatever_the_caller_says(self) -> None:
        spec = SegmentationSpec((rule("transmission", r"^== .*$"),))
        result = segment_text("== One\nalpha\n== Two\nbeta", spec)

        assert [segment.type for segment in result] == ["transmission", "transmission"]
        assert result.segment_types == ("transmission",)

    def test_a_nonsense_vocabulary_works_identically(self) -> None:
        """Nothing may be privileged. If 'plate' works, so must 'zzz'."""
        for name in ("plate", "movement", "zzz", "utterance"):
            spec = SegmentationSpec((rule(name, r"^# .*$"),))
            result = segment_text("# a\nx\n# b\ny", spec)
            assert [segment.type for segment in result] == [name, name]

    def test_labels_come_from_a_named_group(self) -> None:
        spec = SegmentationSpec((rule("part", r"^PART (?P<name>.+)$", label_group="name"),))
        result = segment_text("PART The Return\nbody\nPART The Cost\nbody", spec)

        assert [segment.label for segment in result] == ["The Return", "The Cost"]

    def test_anchor_start_keeps_the_heading_inside_its_segment(self) -> None:
        spec = SegmentationSpec((rule("unit", r"^#.*$"),))
        result = segment_text("#one\nalpha\n#two\nbeta", spec)
        assert result.text_of(0).startswith("#one")

    def test_anchor_end_excludes_the_separator_from_both_neighbours(self) -> None:
        """A separator belongs to neither side, so it is left out of both — a deliberate gap.

        The earlier implementation closed the previous segment at the same offset the next
        one opened, which quietly absorbed each separator into the segment above it.
        """
        spec = SegmentationSpec((rule("unit", r"^-{3}$\n", anchor="end"),))
        result = segment_text("---\nalpha\n---\nbeta", spec)

        assert result.text_of(0) == "alpha\n"
        assert result.text_of(1) == "beta"

    def test_anchor_start_tiles_while_anchor_end_does_not(self) -> None:
        text = "---\nalpha\n---\nbeta"
        separated = segment_text(text, SegmentationSpec((rule("u", r"^-{3}$\n", anchor="end"),)))
        heading = segment_text(text, SegmentationSpec((rule("u", r"^-{3}$\n", anchor="start"),)))

        assert separated.segments[1].start > separated.segments[0].end, "gap is intended"
        assert heading.segments[1].start == heading.segments[0].end, "headings tile"

    def test_a_missing_capture_group_is_a_clean_error(self) -> None:
        spec = SegmentationSpec((rule("unit", r"^# .*$", label_group="absent"),))
        with pytest.raises(SegmentationError, match="capturing group"):
            segment_text("# a\nbody", spec)


class TestNesting:
    SPEC = SegmentationSpec(
        (
            rule("part", r"^PART (?P<n>\w+)$", label_group="n"),
            rule("unit", r"^UNIT (?P<n>\w+)$", label_group="n"),
        )
    )
    TEXT = "PART One\nUNIT A\nalpha\nUNIT B\nbeta\nPART Two\nUNIT C\ngamma\n"

    def test_children_attach_to_the_enclosing_parent(self) -> None:
        result = segment_text(self.TEXT, self.SPEC)
        by_label = {segment.label: segment for segment in result}

        parts = [i for i, s in enumerate(result.segments) if s.type == "part"]
        assert result.segments[parts[0]].label == "One"
        assert by_label["A"].parent == parts[0]
        assert by_label["C"].parent == parts[1]

    def test_sibling_indices_restart_under_each_parent(self) -> None:
        """C is the first unit of part two, not the third unit overall."""
        result = segment_text(self.TEXT, self.SPEC)
        by_label = {segment.label: segment for segment in result}

        assert (by_label["A"].index, by_label["B"].index) == (1, 2)
        assert by_label["C"].index == 1

    def test_locator_path_runs_outermost_first(self) -> None:
        result = segment_text(self.TEXT, self.SPEC)
        position = next(i for i, s in enumerate(result.segments) if s.label == "C")

        assert result.locator_path(position) == [
            {"type": "part", "index": 2, "label": "Two"},
            {"type": "unit", "index": 1, "label": "C"},
        ]

    def test_a_parent_span_contains_its_children(self) -> None:
        result = segment_text(self.TEXT, self.SPEC)
        for position, segment in enumerate(result.segments):
            if segment.parent is not None:
                parent = result.segments[segment.parent]
                assert parent.start <= segment.start
                assert segment.end <= parent.end, f"segment {position} escapes its parent"

    def test_leaves_are_the_deepest_units(self) -> None:
        result = segment_text(self.TEXT, self.SPEC)
        assert [result.segments[i].label for i in result.leaves()] == ["A", "B", "C"]


class TestPreamble:
    def test_content_before_the_first_boundary_is_not_absorbed(self) -> None:
        """Front matter is not part of the first unit, and saying it is would misattribute it."""
        spec = SegmentationSpec((rule("unit", r"^# .*$"),))
        result = segment_text("title page\nby someone\n\n# One\nalpha", spec)

        assert result.preamble_text.startswith("title page")
        assert "title page" not in result.text_of(0)
        assert len(result) == 1

    def test_no_preamble_when_the_text_opens_with_a_boundary(self) -> None:
        spec = SegmentationSpec((rule("unit", r"^# .*$"),))
        result = segment_text("# One\nalpha", spec)
        assert result.preamble_text == ""

    def test_a_spec_that_matches_nothing_yields_one_whole_segment(self) -> None:
        spec = SegmentationSpec((rule("unit", r"^NEVER MATCHES$"),))
        result = segment_text("some text\n\nmore text", spec)

        assert len(result) == 1
        assert result.text_of(0) == "some text\n\nmore text"
        assert result.segments[0].type == "unit"


class TestCoverage:
    @pytest.mark.parametrize(
        "text",
        ["a\n\nb\n\nc", "single", "x\n\n\n\ny", "trailing\n\n"],
    )
    def test_segments_tile_the_text_without_gaps_or_overlap(self, text: str) -> None:
        result = segment_text(text)
        leaves = [result.segments[i] for i in result.leaves()]

        cursor = result.preamble[1]
        for segment in leaves:
            assert segment.start == cursor, "gap or overlap between segments"
            cursor = segment.end
        assert cursor == len(text)

    def test_offsets_index_the_original_text(self) -> None:
        text = "alpha\n\nbeta"
        result = segment_text(text)
        for position, segment in enumerate(result.segments):
            assert result.text_of(position) == text[segment.start : segment.end]


class TestSpecValidation:
    def test_a_spec_needs_a_rule(self) -> None:
        with pytest.raises(SegmentationError, match="at least one rule"):
            SegmentationSpec(())

    def test_duplicate_types_are_rejected(self) -> None:
        with pytest.raises(SegmentationError, match="distinct"):
            SegmentationSpec((rule("unit", r"^a$"), rule("unit", r"^b$")))

    def test_a_rule_needs_a_type(self) -> None:
        with pytest.raises(SegmentationError, match="needs a type"):
            SegmentRule(type="", pattern=re.compile("x"))

    def test_unknown_anchor_is_rejected(self) -> None:
        with pytest.raises(SegmentationError, match="unknown anchor"):
            SegmentRule(type="unit", pattern=re.compile("x"), anchor="middle")  # type: ignore[arg-type]

    def test_default_spec_declares_its_vocabulary(self) -> None:
        assert DEFAULT_SPEC.segment_types == ["section"]


@pytest.fixture(scope="module")
def body() -> str:
    """The novel from its first sentence, excluding front matter and the contents table."""
    text = (FIXTURE_A / "pride-and-prejudice.txt").read_text(encoding="utf-8")
    return text[text.index("It is a truth universally acknowledged") :]


class TestAgainstTheRealFixture:
    """Synthetic inputs are tidy. A real edition's typography is not."""

    def test_a_heading_rule_finds_the_chapters(self, body: str) -> None:
        spec = SegmentationSpec(
            (
                SegmentRule(
                    type="chapter",
                    pattern=re.compile(r"(?i)\bchapter\s+(?P<numeral>[ivxlc]+)\s*\."),
                    label_group="numeral",
                ),
            )
        )
        result = segment_text(body, spec)

        # 58, not 61. The body slice begins at the first sentence, so chapter I's heading
        # sits just before it and is not matched; and two further chapters carry their
        # heading inside an illustration block rather than as a running head, exactly as
        # recorded in fixtures/a/expectations.json. Asserting the true number keeps that
        # known gap visible rather than papering over it with a lenient bound.
        assert len(result) == 58
        assert result.segments[0].label.upper() == "II"
        assert result.segments[-1].label.upper() == "LXI"
        assert all(segment.type == "chapter" for segment in result)

    def test_chapter_indices_are_sequential(self, body: str) -> None:
        """Index is ordinal position among siblings, not the numeral in the heading."""
        spec = SegmentationSpec(
            (
                SegmentRule(
                    type="chapter",
                    pattern=re.compile(r"(?i)\bchapter\s+(?P<numeral>[ivxlc]+)\s*\."),
                    label_group="numeral",
                ),
            )
        )
        result = segment_text(body, spec)
        assert [segment.index for segment in result] == list(range(1, len(result) + 1))

    def test_segments_tile_the_body(self, body: str) -> None:
        result = segment_text(body)
        cursor = result.preamble[1]
        for position in result.leaves():
            assert result.segments[position].start == cursor
            cursor = result.segments[position].end
        assert cursor == len(body)

    def test_default_segmentation_is_paragraph_grained(self, body: str) -> None:
        result = segment_text(body)
        assert len(result) > 1000, "blank-line blocks should be plentiful in a novel"

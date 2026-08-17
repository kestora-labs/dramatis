"""Finding a quotation again after the text around it has been edited.

The tests are written as edits rather than as inputs: an editor inserts a paragraph, fixes a
typo inside a quoted line, moves a scene, cuts it. Each rung of the ladder exists because
one of those is common.
"""

from __future__ import annotations

from dramatis.reanchor import MIN_SIMILARITY, Anchor, occurrences, reanchor, reanchor_selector
from dramatis.text import normalise_whitespace

ORIGINAL = normalise_whitespace(
    """
    Ada met Bram at the gate, and neither of them spoke of it afterwards.

    Later, in the long room, Cai said that the weather had turned.

    Bram did not answer her.
    """
)


def anchored(anchor: Anchor | None, text: str) -> str:
    assert anchor is not None
    return text[anchor.start : anchor.end]


class TestExact:
    def test_finds_a_quotation_that_has_not_moved(self):
        found = reanchor(ORIGINAL, "Cai said that the weather had turned")

        assert found is not None
        assert found.method == "exact"
        assert found.similarity == 1.0
        assert anchored(found, ORIGINAL) == "Cai said that the weather had turned"

    def test_survives_a_paragraph_inserted_before_the_quotation(self):
        # The acceptance case for phase 2, and the reason a selector is stored rather than
        # an offset: everything after the insertion moves, and the string does not.
        edited = normalise_whitespace(
            """
            Ada met Bram at the gate, and neither of them spoke of it afterwards.

            A paragraph that was not there before, describing the weather at length
            and mentioning nobody in particular.

            Later, in the long room, Cai said that the weather had turned.

            Bram did not answer her.
            """
        )

        before = reanchor(ORIGINAL, "Cai said that the weather had turned")
        after = reanchor(edited, "Cai said that the weather had turned")

        assert after is not None and after.method == "exact"
        assert anchored(after, edited) == "Cai said that the weather had turned"
        assert after.start != before.start, "the offset should have moved; the anchor should not"

    def test_survives_the_quotation_moving_to_the_front(self):
        moved = normalise_whitespace(
            """
            Bram did not answer her.

            Later, in the long room, Cai said that the weather had turned.

            Ada met Bram at the gate, and neither of them spoke of it afterwards.
            """
        )
        found = reanchor(moved, "Cai said that the weather had turned")

        assert found is not None and found.method == "exact"
        assert anchored(found, moved) == "Cai said that the weather had turned"

    def test_is_not_confused_by_whitespace_reflowing(self):
        # Re-wrapping a paragraph changes every line break in it and nothing about the work.
        rewrapped = normalise_whitespace(
            "Ada met Bram at the gate, and neither\nof them spoke\nof it afterwards."
        )
        found = reanchor(rewrapped, "Ada met Bram at the gate")

        assert found is not None and found.method == "exact"


class TestContext:
    """A repeated line is not a failure. The surroundings say which one was meant."""

    REPEATED = normalise_whitespace(
        """
        Ada spoke first. It will not do. Bram looked away.

        Cai waited by the door. It will not do. Nobody moved.
        """
    )

    def test_the_stored_surroundings_choose_between_occurrences(self):
        found = reanchor(
            self.REPEATED,
            "It will not do.",
            prefix="Cai waited by the door. ",
            suffix=" Nobody moved.",
        )

        assert found is not None
        assert found.method == "context"
        assert not found.ambiguous
        assert self.REPEATED[found.start - 10 : found.start].endswith("door. ")

    def test_the_other_surroundings_choose_the_other_one(self):
        found = reanchor(
            self.REPEATED,
            "It will not do.",
            prefix="Ada spoke first. ",
            suffix=" Bram looked away.",
        )

        assert found is not None and found.method == "context"
        assert self.REPEATED[found.end : found.end + 6] == " Bram "

    def test_one_side_of_the_context_is_enough(self):
        found = reanchor(self.REPEATED, "It will not do.", suffix=" Nobody moved.")

        assert found is not None and found.method == "context"
        assert self.REPEATED[found.end : found.end + 8] == " Nobody "

    def test_context_edited_just_outside_the_quotation_still_decides(self):
        # Agreement is counted outwards from the quotation and stops at the first
        # disagreement, so an edit further out costs only the part beyond it.
        edited = normalise_whitespace(
            """
            Ada spoke first. It will not do. Bram looked away.

            Cai waited by the green door. It will not do. Nobody moved.
            """
        )
        found = reanchor(
            edited, "It will not do.", prefix="Cai waited by the door. ", suffix=" Nobody moved."
        )

        assert found is not None and found.method == "context"
        assert edited[found.end : found.end + 8] == " Nobody "

    def test_an_undecidable_repeat_is_reported_rather_than_guessed(self):
        # Two identical occurrences with no context to separate them. Picking the first and
        # saying nothing would be a citation the reader cannot check.
        twice = normalise_whitespace("It will not do. It will not do.")
        found = reanchor(twice, "It will not do.")

        assert found is not None
        assert found.ambiguous
        assert not found.certain

    def test_the_offset_hint_breaks_a_tie_it_cannot_settle(self):
        twice = normalise_whitespace("It will not do. It will not do.")
        found = reanchor(twice, "It will not do.", hint=16)

        assert found is not None and found.ambiguous
        assert found.start == 16


class TestFuzzy:
    """The edit was inside the quotation, so there is nothing verbatim left to find."""

    def test_recovers_a_quotation_with_a_typo_fixed_inside_it(self):
        edited = ORIGINAL.replace("neither of them spoke", "neither of them spoke aloud")
        found = reanchor(
            edited,
            "Ada met Bram at the gate, and neither of them spoke of it afterwards.",
        )

        assert found is not None
        assert found.method == "fuzzy"
        assert found.similarity >= MIN_SIMILARITY
        assert "Ada met Bram at the gate" in anchored(found, edited)

    def test_a_fuzzy_match_never_claims_to_be_exact(self):
        edited = ORIGINAL.replace("the weather had turned", "the weather had finally turned")
        found = reanchor(edited, "Cai said that the weather had turned")

        assert found is not None
        assert found.method == "fuzzy"
        assert found.similarity < 1.0
        assert not found.certain

    def test_it_marks_the_matching_part_rather_than_the_whole_window(self):
        edited = ORIGINAL.replace("Cai said", "Cai eventually said")
        found = reanchor(edited, "Cai said that the weather had turned")

        assert found is not None
        marked = anchored(found, edited)
        assert marked.startswith("Cai")
        assert marked.endswith("turned")

    def test_a_quotation_that_was_cut_has_no_anchor(self):
        # The useful answer. Re-pointing at whatever scored highest would make every
        # citation in the project unfalsifiable.
        cut = normalise_whitespace("Ada met Bram at the gate. Bram did not answer her.")
        assert reanchor(cut, "Cai said that the weather had turned") is None

    def test_a_different_sentence_sharing_vocabulary_is_not_a_match(self):
        other = normalise_whitespace(
            "The weather had turned, and Ada said nothing at all about Cai or the room."
        )
        found = reanchor(other, "Cai said that the weather had turned")

        assert found is None or found.similarity >= MIN_SIMILARITY

    def test_the_threshold_is_the_thing_that_refuses(self):
        assert MIN_SIMILARITY > 0.5, "a coin-toss threshold would offer any passage at all"

    def test_a_short_quotation_survives_a_word_inserted_into_it(self):
        # The fragments used to find candidates are cut from the quotation, so at a fixed
        # length every fragment of a short quotation spans the edit and the passage is lost
        # for being short rather than for being changed. Fragments shrink instead.
        text = normalise_whitespace("Ada finally met Bram at the gate. Nothing else happened.")
        found = reanchor(text, "Ada met Bram at the gate.")

        assert found is not None
        assert found.method == "fuzzy"
        assert "Bram at the gate" in anchored(found, text)

    def test_an_edit_at_the_front_does_not_lose_the_rest(self):
        text = normalise_whitespace(
            "Quite unexpectedly, Cai said that the weather had turned that evening."
        )
        found = reanchor(text, "Suddenly Cai said that the weather had turned")

        assert found is not None and found.method == "fuzzy"
        assert "the weather had turned" in anchored(found, text)


class TestTheLadderIsOrdered:
    def test_a_verbatim_match_is_preferred_over_a_closer_looking_fuzzy_one(self):
        # If the quotation is still there, no amount of similarity elsewhere should win.
        text = normalise_whitespace(
            "Cai said that the weather had turned. Cai said that the weather had not turned."
        )
        found = reanchor(text, "Cai said that the weather had turned.", suffix=" Cai said")

        assert found is not None
        assert found.method in ("exact", "context")
        assert found.similarity == 1.0


class TestEdges:
    def test_an_empty_quotation_has_no_anchor(self):
        assert reanchor(ORIGINAL, "") is None
        assert reanchor(ORIGINAL, "    ") is None

    def test_an_empty_text_has_no_anchor(self):
        assert reanchor("", "anything") is None

    def test_the_whole_text_as_the_quotation(self):
        found = reanchor(ORIGINAL, ORIGINAL)
        assert found is not None and found.start == 0 and found.end == len(ORIGINAL)


class TestOccurrences:
    def test_it_finds_every_place_including_overlapping_ones(self):
        assert occurrences("aaaa", "aa") == [0, 1, 2]

    def test_it_stops_at_the_limit(self):
        assert len(occurrences("a" * 100, "a", limit=5)) == 5

    def test_an_empty_needle_occurs_nowhere(self):
        assert occurrences("abc", "") == []


class TestSelectorShape:
    def test_it_reads_a_selector_as_the_schema_stores_one(self):
        found = reanchor_selector(
            ORIGINAL,
            {
                "exact": "Cai said that the weather had turned",
                "prefix": "Later, in the long room, ",
                "suffix": ".",
            },
        )

        assert found is not None
        assert anchored(found, ORIGINAL) == "Cai said that the weather had turned"

    def test_it_copes_with_a_selector_carrying_only_a_quotation(self):
        found = reanchor_selector(ORIGINAL, {"exact": "Bram did not answer her"})
        assert found is not None and found.method == "exact"

    def test_it_treats_a_null_context_as_absent(self):
        found = reanchor_selector(
            ORIGINAL, {"exact": "Bram did not answer her", "prefix": None, "suffix": None}
        )
        assert found is not None

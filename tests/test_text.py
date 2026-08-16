"""Tests for text normalisation and hashing.

Two normalisations exist and they must not be confused. Line-ending normalisation is for
hashing and is nearly lossless. Whitespace collapsing is for quotation matching and is
lossy. Using the lossy one to hash would make genuinely different texts share a revision
identifier.
"""

from __future__ import annotations

import hashlib

from dramatis.text import (
    contains_quotation,
    content_hash,
    find_quotation,
    normalise_line_endings,
    normalise_whitespace,
    revision_hash,
)


class TestLineEndings:
    def test_crlf_becomes_lf(self) -> None:
        assert normalise_line_endings("a\r\nb") == "a\nb"

    def test_bare_cr_becomes_lf(self) -> None:
        assert normalise_line_endings("a\rb") == "a\nb"

    def test_byte_order_mark_is_dropped(self) -> None:
        assert normalise_line_endings("﻿hello") == "hello"

    def test_indentation_and_blank_lines_survive(self) -> None:
        """These are part of the text; a change to them is a real change."""
        original = "one\n\n    two   \n"
        assert normalise_line_endings(original) == original


class TestContentHash:
    def test_platform_line_endings_do_not_change_the_hash(self) -> None:
        """A revision identifier must not depend on who checked the file out."""
        assert content_hash("a\r\nb\r\n") == content_hash("a\nb\n")

    def test_hash_is_plain_sha256_of_the_normalised_bytes(self) -> None:
        expected = hashlib.sha256(b"a\nb\n").hexdigest()
        assert content_hash("a\r\nb\r\n") == expected

    def test_different_text_hashes_differently(self) -> None:
        assert content_hash("Elizabeth") != content_hash("Elisabeth")

    def test_whitespace_differences_do_change_the_hash(self) -> None:
        """The lossy normalisation must not leak into hashing."""
        assert content_hash("a  b") != content_hash("a b")


class TestRevisionHash:
    def test_single_document_revision_hashes_like_its_document(self) -> None:
        """The least surprising result, and what the schema's own wording describes."""
        text = "It is a truth universally acknowledged"
        assert revision_hash([text]) == content_hash(text)

    def test_documents_are_concatenated_in_order(self) -> None:
        assert revision_hash(["one", "two"]) == content_hash("onetwo")

    def test_reordering_documents_changes_the_hash(self) -> None:
        assert revision_hash(["one", "two"]) != revision_hash(["two", "one"])

    def test_empty_revision_is_the_empty_hash(self) -> None:
        assert revision_hash([]) == hashlib.sha256(b"").hexdigest()


class TestQuotationMatching:
    def test_line_wrapping_does_not_defeat_a_quotation(self) -> None:
        source = "In vain have I\nstruggled. It will not do."
        assert contains_quotation(source, "In vain have I struggled.")

    def test_case_is_not_folded(self) -> None:
        assert not contains_quotation("In vain have I struggled.", "in vain have i struggled.")

    def test_punctuation_is_not_substituted(self) -> None:
        """Curly and straight quotation marks are different characters, and stay different."""
        assert not contains_quotation("“tolerable”", '"tolerable"')

    def test_absent_quotation_returns_none(self) -> None:
        assert find_quotation("a b c", "d e") is None

    def test_empty_quotation_matches_nothing(self) -> None:
        assert find_quotation("a b c", "   ") is None

    def test_offset_indexes_the_normalised_source(self) -> None:
        source = "one\n\n  two   three"
        offset = find_quotation(source, "two three")
        assert offset is not None
        assert normalise_whitespace(source)[offset:] == "two three"
